"""Verifier node for evidence-backed reporting."""

from __future__ import annotations

from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.trace import create_trace_event
from app.config import config

try:
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_qwq import ChatQwen
except Exception:  # pragma: no cover
    ChatPromptTemplate = None
    ChatQwen = None


class VerifierOutput(BaseModel):
    """Verifier structured output."""

    passed: bool = Field(...)
    findings: list[str] = Field(default_factory=list)
    suggested_next_steps: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)


def _heuristic_verify(state: PlanExecuteState) -> VerifierOutput:
    response = state.get("response", "")
    past_steps = state.get("past_steps", [])
    findings: list[str] = []
    suggested: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []

    if len(past_steps) < 2:
        findings.append("执行步骤过少，证据覆盖不足。")
        suggested.append("补充至少一次指标查询和一次日志或历史案例查询。")
        missing.append("跨来源证据")

    lowered = response.lower()
    if "根因" in response and "证据" not in response:
        findings.append("报告包含根因结论，但未明确标出证据。")
        suggested.append("补充日志、指标或历史案例证据。")
        missing.append("根因证据")

    if "影响" not in response and "范围" not in response:
        findings.append("报告缺少影响范围说明。")
        suggested.append("补充受影响服务、时间窗口和风险范围。")
        missing.append("影响范围")

    if any(keyword in lowered for keyword in ["重启", "扩容", "kill", "删除"]) and "风险" not in response:
        findings.append("存在高风险建议但未给出风险提示。")
        suggested.append("补充高风险操作的回滚条件和风险提示。")
        warnings.append("高风险建议缺少风险说明")

    return VerifierOutput(
        passed=not findings,
        findings=findings,
        suggested_next_steps=suggested,
        missing_evidence=missing,
        risk_warnings=warnings,
    )


async def verifier(state: PlanExecuteState) -> dict[str, Any]:
    """LangGraph node: verify whether the report is evidence-backed."""
    logger.info("=== Verifier：检查报告可信度 ===")
    session_id = state.get("session_id", "default")
    response = state.get("response", "")
    past_steps = state.get("past_steps", [])

    result: VerifierOutput
    if ChatQwen and ChatPromptTemplate and config.dashscope_api_key and response.strip():
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "你是 AIOps Verifier。检查报告是否有证据支撑、是否存在无根据猜测、"
                    "是否说明影响范围、是否包含高风险建议但缺少风险提示。"
                    "如果不通过，请提供 suggested_next_steps。",
                ),
                ("user", "原始任务:\n{task}\n\n执行历史:\n{history}\n\n待验证报告:\n{report}"),
            ]
        )
        llm = ChatQwen(model=config.rag_model, api_key=config.dashscope_api_key, temperature=0)
        chain = prompt | llm.with_structured_output(VerifierOutput)
        history_text = "\n\n".join(
            f"步骤: {step}\n结果: {result_text}" for step, result_text in past_steps
        )
        try:
            result = await chain.ainvoke(
                {
                    "task": state.get("input", ""),
                    "history": history_text or "无",
                    "report": response,
                }
            )
        except Exception as exc:
            logger.warning(f"Verifier LLM failed, fallback to heuristics: {exc}")
            result = _heuristic_verify(state)
    else:
        result = _heuristic_verify(state)

    verifier_result = result.model_dump()
    if not verifier_result["passed"]:
        trace_event = create_trace_event(
            session_id=session_id,
            node="verifier",
            status="warning",
            title="Verifier requested more evidence",
            result_summary="; ".join(verifier_result["findings"]),
            metadata=verifier_result,
        )
        return {
            "response": "",
            "plan": list(dict.fromkeys(verifier_result["suggested_next_steps"])),
            "verifier_result": verifier_result,
            "trace_events": [trace_event],
        }

    trace_event = create_trace_event(
        session_id=session_id,
        node="verifier",
        status="success",
        title="Verifier passed",
        result_summary="Report is sufficiently evidence-backed",
        metadata=verifier_result,
    )
    return {
        "verifier_result": verifier_result,
        "trace_events": [trace_event],
    }
