"""Typed remediation action schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RemediationActionDefinition(BaseModel):
    """Static remediation action definition."""

    action_id: str
    title: str
    description: str
    profile_ids: list[str] = Field(default_factory=list)
    risk_level: str
    dry_run_supported: bool = True
    approval_required: bool = False
    default_params: dict[str, Any] = Field(default_factory=dict)
    expected_benefit: str = ""
    safety_note: str = ""


class RemediationCandidate(BaseModel):
    """Diagnosis-time remediation candidate."""

    action_id: str
    title: str
    description: str
    risk_level: str
    dry_run_supported: bool
    approval_required: bool
    reason: str
    expected_benefit: str
    safety_note: str
    params: dict[str, Any] = Field(default_factory=dict)
