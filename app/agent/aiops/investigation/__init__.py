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
from .disk_engine import (
    DISK_PRESSURE_PROFILE_ID,
    build_follow_up_tasks,
    build_initial_disk_tasks,
    build_disk_investigation_report,
    compute_no_progress_rounds,
    decide_disk_stop,
    is_disk_pressure_profile,
    summarize_evidence_store,
    update_disk_evidence_store,
    verify_disk_investigation_report,
)
from .profiles import (
    DEFAULT_PATROL_PROFILE,
    DISK_PRESSURE_PROFILE,
    get_profile,
    infer_diagnosis_intent,
    resolve_selected_profile,
    supports_profile_execution,
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
    "DISK_PRESSURE_PROFILE",
    "DISK_PRESSURE_PROFILE_ID",
    "build_evidence_store",
    "build_follow_up_tasks",
    "build_initial_disk_tasks",
    "build_disk_investigation_report",
    "compute_no_progress_rounds",
    "count_evidence_statuses",
    "decide_disk_stop",
    "is_disk_pressure_profile",
    "record_evidence_attempt",
    "summarize_evidence_store",
    "get_profile",
    "infer_diagnosis_intent",
    "resolve_selected_profile",
    "supports_profile_execution",
    "decide_stop_action",
    "update_disk_evidence_store",
    "verify_disk_investigation_report",
]
