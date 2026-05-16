"""Verifier node for evidence-backed reporting."""

from __future__ import annotations

from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.disk_cleanup import build_disk_verifier_findings, is_disk_cleanup_request
from app.agent.aiops.investigation import StopDecision, StopDecisionType
from app.agent.aiops.investigation import is_disk_pressure_profile, verify_disk_investigation_report
from app.agent.aiops.patrol import build_patrol_verifier_findings
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.trace import create_trace_event
from app.config import config
from app.core.llm_factory import llm_factory

try:
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_qwq import ChatQwen
except Exception:  # pragma: no cover
    ChatPromptTemplate = None
    ChatQwen = None


def _model_to_dict(model: object) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


class VerifierOutput(BaseModel):
    """Verifier structured output."""

    passed: bool = Field(...)
    findings: list[str] = Field(default_factory=list)
    suggested_next_steps: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    risk_warnings: list[str] = Field(default_factory=list)


def _disk_investigation_verify(state: PlanExecuteState) -> VerifierOutput:
    findings, missing, warnings = verify_disk_investigation_report(state)
    return VerifierOutput(
        passed=not findings,
        findings=findings,
        suggested_next_steps=[],
        missing_evidence=missing,
        risk_warnings=warnings,
    )


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


def _generic_template_verify(state: PlanExecuteState) -> VerifierOutput:
    response = state.get("response", "")
    past_steps = state.get("past_steps", [])
    findings: list[str] = []
    suggested: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []

    if len(past_steps) < 2:
        findings.append("通用诊断链路收集到的证据步骤过少。")
        suggested.append("至少补充一次本地知识库检索，并在需要时补充公开文档参考。")
        missing.append("execution_history")

    if not response.strip():
        findings.append("最终报告为空。")
        suggested.append("基于当前执行历史生成最终报告。")
        missing.append("final_report")

    lowered = response.lower()
    if "未执行任何" not in response and "没有执行任何" not in response and "no dangerous operation" not in lowered:
        findings.append("报告没有明确说明未执行任何危险操作。")
        suggested.append("在风险提示中明确声明未执行任何删除、覆盖、pull 或 prune 操作。")
        warnings.append("missing_safety_disclaimer")

    used_web_search = any("web_search" in step for step, _ in past_steps)
    if used_web_search and "联网搜索补充资料" not in response:
        findings.append("报告使用了联网资料，但没有单独区分外部参考。")
        suggested.append("增加“联网搜索补充资料”章节，并附上标题与链接。")
        missing.append("external_reference_section")

    return VerifierOutput(
        passed=not findings,
        findings=findings,
        suggested_next_steps=suggested,
        missing_evidence=missing,
        risk_warnings=warnings,
    )


def _should_finalize_legacy_generic(state: PlanExecuteState) -> bool:
    plan_source = state.get("plan_source", "")
    return plan_source in {
        "generic_llm",
        "generic_template_fallback",
        "controlled_no_profile",
        "legacy_generic_disabled",
    }


async def verifier(state: PlanExecuteState) -> dict[str, object]:
    """LangGraph node: verify whether the report is evidence-backed."""
    logger.info("=== Verifier ===")
    session_id = state.get("session_id", "default")
    response = state.get("response", "")
    past_steps = state.get("past_steps", [])

    result: VerifierOutput
    if is_disk_pressure_profile(state.get("selected_profile")):
        result = _disk_investigation_verify(state)
    elif is_disk_cleanup_request(state.get("input", ""), state.get("matched_skills", [])):
        result = _disk_verify(state)
    elif state.get("plan_source") == "controlled_no_profile":
        result = VerifierOutput(
            passed=True,
            findings=[],
            suggested_next_steps=[],
            missing_evidence=["execution_profile"],
            risk_warnings=["controlled_stop_before_legacy_generic_chain"],
        )
    elif state.get("plan_source") == "generic_template_fallback":
        result = _generic_template_verify(state)
    elif state.get("target_alert"):
        result = _heuristic_verify(state)
    elif ChatQwen and ChatPromptTemplate and config.get_llm_api_key() and response.strip():
        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    (
                        "You are an AIOps verifier. Check whether the report is backed by tool evidence, "
                        "avoids unsupported guesses, covers impact scope, and adds risk warnings for high-risk suggestions. "
                        "web_search evidence is external reference only and cannot replace local metrics, logs, or tickets. "
                        "If web_search is cited, the report must clearly separate it from local evidence and include title and link. "
                        "If it fails, return concise suggested_next_steps."
                    ),
                ),
                ("user", "Task:\n{task}\n\nExecution history:\n{history}\n\nReport:\n{report}"),
            ]
        )
        llm = llm_factory.create_qwen_chat_model(
            preferred_model=config.rag_model,
            temperature=0,
            streaming=True,
        )
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

    verifier_result = _model_to_dict(result)
    if not verifier_result["passed"]:
        trace_event = create_trace_event(
            session_id=session_id,
            node="verifier",
            status="warning",
            title="Verifier requested more evidence",
            result_summary="; ".join(verifier_result["findings"]),
            metadata=verifier_result,
        )
        if _should_finalize_legacy_generic(state):
            return {
                "verifier_result": verifier_result,
                "stop_decision": _model_to_dict(
                    StopDecision(
                        decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                        reason="Legacy generic diagnosis is not allowed to refill free-text plans.",
                        missing_slots=list(verifier_result.get("missing_evidence", [])),
                    )
                ),
                "trace_events": [trace_event],
            }
        if is_disk_pressure_profile(state.get("selected_profile")):
            return {
                "verifier_result": verifier_result,
                "trace_events": [trace_event],
            }
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
