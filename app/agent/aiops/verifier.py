"""Verifier node for evidence-backed reporting."""

from __future__ import annotations

from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.disk_cleanup import (
    build_disk_verifier_findings,
    is_disk_cleanup_request,
)
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


def _disk_verify(state: PlanExecuteState) -> VerifierOutput:
    findings, suggested, missing, warnings = build_disk_verifier_findings(
        state.get("response", ""),
        state.get("past_steps", []),
    )
    return VerifierOutput(
        passed=not findings,
        findings=findings,
        suggested_next_steps=suggested,
        missing_evidence=missing,
        risk_warnings=warnings,
    )


def _heuristic_verify(state: PlanExecuteState) -> VerifierOutput:
    if is_disk_cleanup_request(state.get("input", ""), state.get("matched_skills", [])):
        return _disk_verify(state)

    response = state.get("response", "")
    past_steps = state.get("past_steps", [])
    active_alerts = state.get("active_alerts", [])
    findings: list[str] = []
    suggested: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []

    if state.get("mode") == "default" and not active_alerts and "未检测到活跃告警" in response:
        return VerifierOutput()

    if len(past_steps) < 2:
        findings.append("执行步骤过少，缺少支撑结论的工具证据。")
        suggested.append("补充至少两步工具证据，再生成诊断报告。")
        missing.append("执行步骤")

    lowered = response.lower()
    if "根因" in response and "证据" not in response:
        findings.append("报告提到了根因，但没有明确对应证据。")
        suggested.append("补充关键证据段落，并将证据与根因关联。")
        missing.append("关键证据")

    if "影响范围" not in response:
        findings.append("报告缺少影响范围说明。")
        suggested.append("补充受影响主机、服务或业务范围。")
        missing.append("影响范围")

    if any(keyword in lowered for keyword in ["删除", "kill", "prune"]) and "风险" not in response:
        findings.append("报告包含高风险建议，但缺少风险提示。")
        suggested.append("为高风险建议补充风险说明和人工确认要求。")
        warnings.append("存在高风险操作建议")

    return VerifierOutput(
        passed=not findings,
        findings=findings,
        suggested_next_steps=suggested,
        missing_evidence=missing,
        risk_warnings=warnings,
    )


async def verifier(state: PlanExecuteState) -> dict[str, Any]:
    """LangGraph node: verify whether the report is evidence-backed."""
    logger.info("=== Verifier ===")
    session_id = state.get("session_id", "default")
    response = state.get("response", "")
    past_steps = state.get("past_steps", [])

    result: VerifierOutput
    if is_disk_cleanup_request(state.get("input", ""), state.get("matched_skills", [])):
        result = _disk_verify(state)
    elif ChatQwen and ChatPromptTemplate and config.dashscope_api_key and response.strip():
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    (
                        "You are an AIOps verifier. Check whether the report is backed by tool evidence, "
                        "avoids unsupported guesses, covers impact scope, and adds risk warnings for high-risk suggestions. "
                        "If it fails, return concise suggested_next_steps."
                    ),
                ),
                ("user", "Task:\n{task}\n\nExecution history:\n{history}\n\nReport:\n{report}"),
            ]
        )
        llm = ChatQwen(model=config.rag_model, api_key=config.dashscope_api_key, temperature=0)
        chain = prompt | llm.with_structured_output(VerifierOutput)
        history_text = "\n\n".join(f"Step: {step}\nResult: {result_text}" for step, result_text in past_steps)
        try:
            result = await chain.ainvoke(
                {
                    "task": state.get("input", ""),
                    "history": history_text or "(empty)",
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
