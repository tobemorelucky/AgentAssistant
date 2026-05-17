"""Investigation engine foundations for governed AIOps diagnosis."""

from .evidence import build_evidence_store, count_evidence_statuses, record_evidence_attempt
from .models import (
    DiagnosisIntent,
    DiagnosisProfile,
    EvidenceRecord,
    EvidenceStatus,
    InvestigationTask,
    StopDecision,
    StopDecisionType,
)
from .patrol_dispatch import (
    PATROL_DISPATCH_PROFILE_ID,
    build_no_alert_patrol_report,
    build_unsupported_profile_report,
    resolve_alert_profile_id,
    select_target_alert,
    suggest_future_profile_id,
)
from .profiles import (
    CPU_PRESSURE_PROFILE,
    DEFAULT_PATROL_PROFILE,
    DISK_PRESSURE_PROFILE,
    MEMORY_PRESSURE_PROFILE,
    PATROL_DISPATCH_PROFILE,
    get_profile,
    infer_diagnosis_intent,
    resolve_selected_profile,
    supports_profile_execution,
)
from .runtime import (
    CpuInvestigationRuntime,
    DiskInvestigationRuntime,
    InvestigationRuntime,
    MemoryInvestigationRuntime,
    get_runtime,
    has_runtime,
)
from .stop_controller import decide_stop_action

__all__ = [
    "DiagnosisIntent",
    "DiagnosisProfile",
    "EvidenceRecord",
    "EvidenceStatus",
    "InvestigationTask",
    "StopDecision",
    "StopDecisionType",
    "DEFAULT_PATROL_PROFILE",
    "PATROL_DISPATCH_PROFILE",
    "PATROL_DISPATCH_PROFILE_ID",
    "DISK_PRESSURE_PROFILE",
    "MEMORY_PRESSURE_PROFILE",
    "CPU_PRESSURE_PROFILE",
    "CpuInvestigationRuntime",
    "DiskInvestigationRuntime",
    "MemoryInvestigationRuntime",
    "InvestigationRuntime",
    "build_evidence_store",
    "build_no_alert_patrol_report",
    "build_unsupported_profile_report",
    "count_evidence_statuses",
    "decide_stop_action",
    "get_profile",
    "get_runtime",
    "has_runtime",
    "infer_diagnosis_intent",
    "record_evidence_attempt",
    "resolve_alert_profile_id",
    "resolve_selected_profile",
    "select_target_alert",
    "suggest_future_profile_id",
    "supports_profile_execution",
]
