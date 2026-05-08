"""AIOps Agent workflow state."""

from __future__ import annotations

import operator
from typing import Annotated, Any, List, TypedDict


class PlanExecuteState(TypedDict, total=False):
    """Plan-Execute-Replan state with governance metadata."""

    session_id: str
    input: str
    mode: str
    entry_node: str
    status: str
    plan: List[str]
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
    incident_record: dict[str, Any]
    feedback: dict[str, Any]
    generated_skill_draft: str | None
