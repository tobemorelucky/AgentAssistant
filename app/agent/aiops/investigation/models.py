"""Core investigation data models for the next AIOps engine."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class DiagnosisIntent(StrEnum):
    """Top-level intent classification for a diagnosis request."""

    STATUS_QUERY = "status_query"
    INCIDENT_DIAGNOSIS = "incident_diagnosis"
    REMEDIATION_REQUEST = "remediation_request"
    DEFAULT_PATROL = "default_patrol"
    KNOWLEDGE_ONLY = "knowledge_only"


class EvidenceStatus(StrEnum):
    """Collection state for one evidence slot."""

    MISSING = "missing"
    COLLECTED = "collected"
    FAILED = "failed"
    PARTIAL = "partial"


class StopDecisionType(StrEnum):
    """Stop controller output."""

    CONTINUE = "continue"
    FINALIZE = "finalize"
    FINALIZE_WITH_LIMITATIONS = "finalize_with_limitations"


class DiagnosisProfile(BaseModel):
    """Structured diagnosis profile used by the future investigation engine."""

    profile_id: str
    supported_intents: list[DiagnosisIntent] = Field(default_factory=list)
    resource_type: str = "generic"
    required_evidence_slots: list[str] = Field(default_factory=list)
    conditional_evidence_slots: list[str] = Field(default_factory=list)
    reference_evidence_slots: list[str] = Field(default_factory=list)
    stop_rules: dict[str, Any] = Field(default_factory=dict)
    report_schema: list[str] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    """One evidence slot and its collection state."""

    slot: str
    status: EvidenceStatus = EvidenceStatus.MISSING
    source: str = ""
    payload: Any = None
    attempts: int = 0
    quality: Literal["unknown", "low", "medium", "high"] = "unknown"
    error_message: str = ""


class InvestigationTask(BaseModel):
    """A structured tool task for evidence collection."""

    slot: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    required: bool = True
    reason: str = ""


class StopDecision(BaseModel):
    """Decision emitted by the stop controller."""

    decision: StopDecisionType = StopDecisionType.CONTINUE
    reason: str = ""
    missing_slots: list[str] = Field(default_factory=list)
