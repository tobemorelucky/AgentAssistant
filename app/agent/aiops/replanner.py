"""Replanner node for evidence-driven AIOps runtimes."""

from __future__ import annotations

from app.agent.aiops.followup_report import build_followup_enrichment_report
from app.agent.aiops.investigation import StopDecision, StopDecisionType, build_evidence_store, get_profile, get_runtime
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.trace import create_trace_event


FOLLOWUP_ENRICHMENT_PLAN_SOURCES = {
    "followup_local_enrichment",
    "followup_external_enrichment",
}


def _model_to_dict(model: object) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


async def replanner(state: PlanExecuteState) -> dict[str, object]:
    """LangGraph node: decide whether to continue collecting evidence or finalize."""
    session_id = state.get("session_id", "default")
    plan = list(state.get("plan", []))
    verifier_result = state.get("verifier_result", {})
    plan_source = state.get("plan_source", "")
    selected_profile = state.get("selected_profile") or {}
    runtime = get_runtime(selected_profile.get("profile_id") if isinstance(selected_profile, dict) else None)

    if runtime is not None and plan_source == "investigation_runtime":
        no_progress_rounds = runtime.compute_no_progress_rounds(state)
        state_for_runtime = dict(state)
        state_for_runtime["no_progress_rounds"] = no_progress_rounds
        stop_decision = runtime.decide_stop(state_for_runtime)

        if plan:
            return {
                "no_progress_rounds": no_progress_rounds,
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="replanner",
                        status="success",
                        title=f"{selected_profile.get('profile_id')} investigation continues",
                        result_summary=runtime.summarize_evidence_store(state_for_runtime),
                    )
                ],
            }

        escalation_builder = getattr(runtime, "build_escalation", None)
        escalation_payload = escalation_builder(state_for_runtime) if callable(escalation_builder) else None
        if escalation_payload and stop_decision.decision != StopDecisionType.FINALIZE_WITH_LIMITATIONS:
            escalated_profile = escalation_payload.get("selected_escalation_profile") or {}
            escalated_profile_id = escalated_profile.get("profile_id")
            escalated_runtime = get_runtime(escalated_profile_id)
            escalated_profile_model = get_profile(escalated_profile_id)
            if escalated_runtime is not None and escalated_profile_model is not None:
                next_tasks = escalated_runtime.build_initial_tasks(
                    {
                        **state_for_runtime,
                        "selected_profile": escalated_profile,
                        "target_alert": escalation_payload.get("target_alert") or state_for_runtime.get("target_alert"),
                        "abnormal_findings": escalation_payload.get("abnormal_findings") or [],
                        "selected_escalation_profile": escalated_profile,
                        "escalation_reason": escalation_payload.get("escalation_reason") or "",
                        "host_health_evidence": dict(state_for_runtime.get("evidence_store") or {}),
                        "evidence_store": build_evidence_store(escalated_profile_model),
                        "investigation_round": 0,
                        "no_progress_rounds": 0,
                        "last_investigation_slot": None,
                        "verifier_result": {},
                    }
                )
                return {
                    "plan": next_tasks,
                    "response": "",
                    "plan_source": "investigation_runtime",
                    "selected_profile": escalated_profile,
                    "target_alert": escalation_payload.get("target_alert") or state_for_runtime.get("target_alert"),
                    "abnormal_findings": escalation_payload.get("abnormal_findings") or [],
                    "selected_escalation_profile": escalated_profile,
                    "escalation_reason": escalation_payload.get("escalation_reason") or "",
                    "host_health_evidence": dict(state_for_runtime.get("evidence_store") or {}),
                    "evidence_store": build_evidence_store(escalated_profile_model),
                    "investigation_round": 0,
                    "no_progress_rounds": 0,
                    "last_investigation_slot": None,
                    "verifier_result": {},
                    "trace_events": [
                        create_trace_event(
                            session_id=session_id,
                            node="replanner",
                            status="warning",
                            title="Abnormal findings detected",
                            result_summary=" | ".join(
                                str(item.get("summary"))
                                for item in (escalation_payload.get("abnormal_findings") or [])[:3]
                                if isinstance(item, dict)
                            ),
                        ),
                        create_trace_event(
                            session_id=session_id,
                            node="replanner",
                            status="success",
                            title="Selected escalation target",
                            result_summary=str(escalated_profile_id),
                            metadata={
                                "target_alert": escalation_payload.get("target_alert") or {},
                                "reason": escalation_payload.get("escalation_reason") or "",
                            },
                        ),
                        create_trace_event(
                            session_id=session_id,
                            node="replanner",
                            status="success",
                            title=f"Escalating to {escalated_profile_id}",
                            result_summary=escalation_payload.get("escalation_reason") or "",
                        ),
                    ],
                }

        if verifier_result and not verifier_result.get("passed", True):
            next_tasks = runtime.build_follow_up_tasks(state_for_runtime)
            if next_tasks and stop_decision.decision != StopDecisionType.FINALIZE_WITH_LIMITATIONS:
                return {
                    "plan": next_tasks,
                    "response": "",
                    "investigation_round": int(state.get("investigation_round") or 0) + 1,
                    "no_progress_rounds": no_progress_rounds,
                    "trace_events": [
                        create_trace_event(
                            session_id=session_id,
                            node="replanner",
                            status="warning",
                            title=f"{selected_profile.get('profile_id')} requested follow-up evidence",
                            result_summary=" | ".join(task["tool"] for task in next_tasks),
                            metadata={"missing_slots": verifier_result.get("missing_evidence", [])},
                        )
                    ],
                }

            response = state.get("response", "") or runtime.build_report(state_for_runtime)
            if stop_decision.decision == StopDecisionType.CONTINUE:
                stop_decision = StopDecision(
                    decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                    reason="Investigation produced no new follow-up evidence tasks.",
                    missing_slots=list(verifier_result.get("missing_evidence", [])),
                )
            return {
                "response": response,
                "plan": [],
                "no_progress_rounds": no_progress_rounds,
                "stop_decision": _model_to_dict(stop_decision),
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="replanner",
                        status="warning",
                        title=f"{selected_profile.get('profile_id')} finalized with evidence limits",
                        result_summary=stop_decision.reason,
                    )
                ],
            }

        next_tasks = runtime.build_follow_up_tasks(state_for_runtime)
        if next_tasks and stop_decision.decision != StopDecisionType.FINALIZE_WITH_LIMITATIONS:
            return {
                "plan": next_tasks,
                "response": "",
                "investigation_round": int(state.get("investigation_round") or 0) + 1,
                "no_progress_rounds": no_progress_rounds,
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="replanner",
                        status="success",
                        title=f"{selected_profile.get('profile_id')} planned follow-up evidence",
                        result_summary=" | ".join(task["tool"] for task in next_tasks),
                    )
                ],
            }

        response = runtime.build_report(state_for_runtime)
        return {
            "response": response,
            "plan": [],
            "no_progress_rounds": no_progress_rounds,
            "stop_decision": _model_to_dict(stop_decision),
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="replanner",
                    status="success",
                    title=f"{selected_profile.get('profile_id')} report drafted",
                    result_summary=runtime.summarize_evidence_store(state_for_runtime),
                )
            ],
        }

    if runtime is not None and plan_source in FOLLOWUP_ENRICHMENT_PLAN_SOURCES:
        response = build_followup_enrichment_report(dict(state))
        decision = StopDecision(
            decision=StopDecisionType.FINALIZE,
            reason="Follow-up enrichment gathered additional contextual references.",
        )
        return {
            "response": response,
            "plan": [],
            "stop_decision": _model_to_dict(decision),
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="replanner",
                    status="success",
                    title="Follow-up enrichment report drafted",
                    result_summary=plan_source,
                )
            ],
        }

    return {
        "plan": [],
        "stop_decision": _model_to_dict(
            StopDecision(
                decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                reason="Unsupported legacy replanning path reached after Phase 5 cleanup.",
            )
        ),
        "trace_events": [
            create_trace_event(
                session_id=session_id,
                node="replanner",
                status="warning",
                title="Unsupported legacy replanning path halted",
                result_summary=str(plan_source or "unknown"),
            )
        ],
    }
