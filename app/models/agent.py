"""Agent platform models."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentActionRequest(BaseModel):
    """Approve/reject request."""

    session_id: str = Field(..., description="Session ID")
    action_id: str = Field(..., description="Pending action ID")
    operator: str = Field("anonymous", description="Operator name")
    comment: str = Field("", description="Operator comment")


class PendingActionItem(BaseModel):
    """Pending action item."""

    action_id: str
    tool_name: str
    tool_args_summary: str
    reason: str
    status: str
    created_at: str
    operator: Optional[str] = None
    comment: Optional[str] = None


class PendingActionsResponse(BaseModel):
    """Pending actions response."""

    session_id: str
    status: str
    actions: List[PendingActionItem] = Field(default_factory=list)


class AgentActionResponse(BaseModel):
    """Approve/reject response."""

    code: int = 200
    message: str = "success"
    data: Dict[str, Any]


class SkillDraftSummary(BaseModel):
    """Skill draft summary."""

    name: str
    path: str
    updated_at: str
    description: str = ""


class SkillDraftDetail(BaseModel):
    """Skill draft detail."""

    name: str
    path: str
    content: str
    description: str = ""
    updated_at: str


class SessionFeedbackRequest(BaseModel):
    """Helpful feedback request for a completed AIOps session."""

    session_id: str = Field(..., description="Session ID")
    helpful: bool = Field(False, description="Whether the diagnosis was helpful")
    operator: str = Field("anonymous", description="Operator name")
    comment: str = Field("", description="Optional feedback comment")


class HeartbeatRunRequest(BaseModel):
    """Manual heartbeat trigger request."""

    trigger: str = Field("manual", description="Trigger source")
    session_id: str = Field("heartbeat-manual", description="Session ID prefix for deep diagnosis")


class RemediationDryRunRequest(BaseModel):
    """Dry-run remediation request."""

    action_id: str = Field(..., description="Remediation action ID")
    params: Dict[str, Any] = Field(default_factory=dict, description="Action parameters")
    session_id: str = Field("manual-remediation", description="Related diagnosis session")


class RemediationExecuteRequest(BaseModel):
    """Execute remediation request."""

    dry_run_id: str = Field(..., description="Dry-run ID returned by Host Agent")
    action_id: str = Field(..., description="Remediation action ID")
    approval_token: str = Field("", description="Explicit approval token")
    operator: str = Field("anonymous", description="Operator name")
    reason: str = Field("", description="Execution reason")
    session_id: str = Field("manual-remediation", description="Related diagnosis session")
