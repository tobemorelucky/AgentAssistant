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
