"""AIOps Agent workflow state."""

from __future__ import annotations

import operator
from typing import Annotated, Any, List, TypedDict


class PlanExecuteState(TypedDict, total=False):
    """Plan-Execute-Replan state with governance metadata."""

    session_id: str
    input: str
    mode: str
    diagnosis_intent: str
    selected_profile: dict[str, Any] | None
    evidence_store: dict[str, Any]
    investigation_round: int
    no_progress_rounds: int
    last_investigation_slot: str | None
    stop_decision: dict[str, Any] | None
    plan_source: str
    entry_node: str
    status: str
    plan: List[Any]
    past_steps: Annotated[List[tuple[str, str]], operator.add]
    response: str
    matched_skills: List[dict[str, Any]]
    similar_incidents: List[dict[str, Any]]
    trace_events: Annotated[List[dict[str, Any]], operator.add]
    tools_used: Annotated[List[str], operator.add]
    verifier_result: dict[str, Any]
    pending_action: dict[str, Any] | None
    active_alerts: List[dict[str, Any]]
    target_alert: dict[str, Any] | None
    abnormal_findings: List[dict[str, Any]]
    selected_escalation_profile: dict[str, Any] | None
    escalation_reason: str
    host_health_evidence: dict[str, Any]
    remediation_candidates: List[dict[str, Any]]
    remediation_feedback_failed: bool
    previous_aiops_context: dict[str, Any] | None
    followup_relation: dict[str, Any] | None
    followup_resolution: dict[str, Any] | None
    incident_record: dict[str, Any]
    feedback: dict[str, Any]
    generated_skill_draft: str | None
    memory_persisted: bool
