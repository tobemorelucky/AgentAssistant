"""Verifier node for evidence-backed reporting."""

from __future__ import annotations

from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.disk_cleanup import build_disk_verifier_findings, is_disk_cleanup_request
from app.agent.aiops.patrol import build_patrol_verifier_findings
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
    response = state.get("response", "")
    past_steps = state.get("past_steps", [])

    if is_disk_cleanup_request(state.get("input", ""), state.get("matched_skills", [])):
        return _disk_verify(state)

    if state.get("target_alert"):
        findings, suggested, missing, warnings = build_patrol_verifier_findings(
            response=response,
            target_alert=state.get("target_alert"),
            past_steps=past_steps,
            matched_skills=state.get("matched_skills", []),
        )
        return VerifierOutput(
            passed=not findings,
            findings=findings,
            suggested_next_steps=suggested,
            missing_evidence=missing,
            risk_warnings=warnings,
        )

    findings: list[str] = []
    suggested: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []

    if len(past_steps) < 2:
        findings.append("执行步骤过少，证据不足。")
        suggested.append("补充更多工具证据后再生成报告。")
        missing.append("execution_history")

    if "风险提示" not in response:
        findings.append("报告中缺少风险提示。")
        suggested.append("补充风险提示并说明高风险操作需要人工审批。")
        missing.append("risk_warning")

    lowered = response.lower()
    if any(keyword in lowered for keyword in ["restart", "prune", "rm -rf"]) and "审批" not in response:
        findings.append("报告包含高风险建议但没有人工审批提示。")
        suggested.append("在高风险建议处加入审批提示和风险说明。")
        warnings.append("high_risk_suggestion_without_warning")

    return VerifierOutput(
        passed=not findings,
        findings=findings,
        suggested_next_steps=suggested,
        missing_evidence=missing,
        risk_warnings=warnings,
    )


async def verifier(state: PlanExecuteState) -> dict[str, object]:
    """LangGraph node: verify whether the report is evidence-backed."""
    logger.info("=== Verifier ===")
    session_id = state.get("session_id", "default")
    response = state.get("response", "")
    past_steps = state.get("past_steps", [])

    result: VerifierOutput
    if is_disk_cleanup_request(state.get("input", ""), state.get("matched_skills", [])):
        result = _disk_verify(state)
    elif state.get("target_alert"):
        result = _heuristic_verify(state)
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
