"""Verifier node for evidence-backed reporting."""

from __future__ import annotations

from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.disk_cleanup import build_disk_verifier_findings, is_disk_cleanup_request
from app.agent.aiops.investigation import StopDecision, StopDecisionType, get_runtime
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


def _legacy_disk_verify(state: PlanExecuteState) -> VerifierOutput:
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
        return _legacy_disk_verify(state)

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
        findings.append("执行历史过短，缺少足够证据支持结论。")
        suggested.append("补充至少一个本地证据采集步骤，再重新整理报告。")
        missing.append("execution_history")

    if "风险提示" not in response:
        findings.append("报告缺少风险提示。")
        suggested.append("补充风险提示章节，说明哪些动作需要人工确认。")
        missing.append("risk_warning")

    lowered = response.lower()
    if any(keyword in lowered for keyword in ["restart", "prune", "rm -rf"]) and "人工确认" not in response:
        findings.append("报告包含高风险建议，但没有明确的人工确认提示。")
        suggested.append("为高风险建议补充人工确认和回滚提示。")
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
        findings.append("受控模板链路的执行历史仍然太短，缺少支撑性资料。")
        suggested.append("先补充本地知识或官方公开资料，再整理最终说明。")
        missing.append("execution_history")

    if not response.strip():
        findings.append("模板链路没有产出最终报告。")
        suggested.append("整理当前步骤结果，生成一个受控收口报告。")
        missing.append("final_report")

    lowered = response.lower()
    if "没有执行任何" not in response and "no dangerous operation" not in lowered:
        findings.append("报告缺少“未执行危险操作”的安全声明。")
        suggested.append("补充安全声明，明确没有执行删除、覆盖、pull、prune 等动作。")
        warnings.append("missing_safety_disclaimer")

    used_web_search = any("web_search" in step for step, _ in past_steps)
    if used_web_search and "联网搜索补充资料" not in response:
        findings.append("报告引用了联网搜索结果，但没有单独列出外部参考资料章节。")
        suggested.append("补充“联网搜索补充资料”章节，标明标题、链接和用途。")
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
    selected_profile = state.get("selected_profile") or {}
    runtime = get_runtime(selected_profile.get("profile_id") if isinstance(selected_profile, dict) else None)

    result: VerifierOutput
    if runtime is not None and state.get("plan_source") == "investigation_runtime":
        runtime_result = runtime.verify_report(state)
        result = VerifierOutput(**runtime_result)
    elif is_disk_cleanup_request(state.get("input", ""), state.get("matched_skills", [])):
        result = _legacy_disk_verify(state)
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
        if runtime is not None and state.get("plan_source") == "investigation_runtime":
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
