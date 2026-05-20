"""Verifier node for evidence-backed reporting."""

from __future__ import annotations

from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.investigation import StopDecision, StopDecisionType, get_runtime
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.trace import create_trace_event


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


def _heuristic_verify(state: PlanExecuteState) -> VerifierOutput:
    response = str(state.get("response") or "").strip()
    findings: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []

    if not response:
        findings.append("Final report is empty.")
        missing.append("final_report")

    if "## 风险提示" not in response and "风险提示" not in response:
        findings.append("Report is missing a risk warning section.")
        warnings.append("missing_risk_warning")

    return VerifierOutput(
        passed=not findings,
        findings=findings,
        suggested_next_steps=[],
        missing_evidence=missing,
        risk_warnings=warnings,
    )


async def verifier(state: PlanExecuteState) -> dict[str, object]:
    """LangGraph node: verify whether the report is evidence-backed."""
    logger.info("=== Verifier ===")
    session_id = state.get("session_id", "default")
    selected_profile = state.get("selected_profile") or {}
    runtime = get_runtime(selected_profile.get("profile_id") if isinstance(selected_profile, dict) else None)

    if runtime is not None and state.get("plan_source") == "investigation_runtime":
        runtime_result = runtime.verify_report(state)
        result = VerifierOutput(**runtime_result)
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

        if runtime is not None and state.get("plan_source") == "investigation_runtime":
            stop_decision = state.get("stop_decision") or {}
            if stop_decision.get("decision") not in {"continue"}:
                return {
                    "verifier_result": verifier_result,
                    "stop_decision": _model_to_dict(
                        StopDecision(
                            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                            reason="Runtime verifier failed after bounded evidence collection.",
                            missing_slots=list(verifier_result.get("missing_evidence", [])),
                        )
                    ),
                    "trace_events": [trace_event],
                }
            return {
                "verifier_result": verifier_result,
                "trace_events": [trace_event],
            }

        return {
            "verifier_result": verifier_result,
            "stop_decision": _model_to_dict(
                StopDecision(
                    decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                    reason="Legacy free-form verification refill has been removed.",
                    missing_slots=list(verifier_result.get("missing_evidence", [])),
                )
            ),
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
