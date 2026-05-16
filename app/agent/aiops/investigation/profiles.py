"""Profile registry and intent/profile selection helpers."""

from __future__ import annotations

from .models import DiagnosisIntent, DiagnosisProfile


DEFAULT_PATROL_PROFILE = DiagnosisProfile(
    profile_id="default_patrol",
    supported_intents=[DiagnosisIntent.DEFAULT_PATROL, DiagnosisIntent.INCIDENT_DIAGNOSIS],
    resource_type="service_alert",
    required_evidence_slots=["alert", "metric", "process", "log", "historical", "runbook"],
    conditional_evidence_slots=["memory", "external_reference"],
    reference_evidence_slots=["runbook"],
    stop_rules={"max_rounds": 3, "max_no_progress_rounds": 1, "max_attempts_per_slot": 1},
    report_schema=[
        "任务与对象",
        "已确认事实",
        "关键证据",
        "影响范围",
        "风险提示",
        "处理建议",
    ],
)

DISK_PRESSURE_PROFILE = DiagnosisProfile(
    profile_id="disk_pressure_profile",
    supported_intents=[
        DiagnosisIntent.STATUS_QUERY,
        DiagnosisIntent.INCIDENT_DIAGNOSIS,
        DiagnosisIntent.REMEDIATION_REQUEST,
    ],
    resource_type="disk",
    required_evidence_slots=["disk_usage", "large_directories", "large_files"],
    conditional_evidence_slots=["docker_disk_usage", "deleted_open_files"],
    reference_evidence_slots=["disk_runbook"],
    stop_rules={
        "max_rounds": 4,
        "max_no_progress_rounds": 2,
        "max_attempts_per_slot": 2,
    },
    report_schema=[
        "任务与对象",
        "已确认事实",
        "主要容量来源",
        "候选风险 / 待验证解释",
        "证据缺口",
        "处理建议",
        "风险提示",
        "Runbook 参考",
    ],
)

PROFILE_REGISTRY: dict[str, DiagnosisProfile] = {
    DEFAULT_PATROL_PROFILE.profile_id: DEFAULT_PATROL_PROFILE,
    DISK_PRESSURE_PROFILE.profile_id: DISK_PRESSURE_PROFILE,
}

EXECUTABLE_PROFILE_IDS = {
    DEFAULT_PATROL_PROFILE.profile_id,
    DISK_PRESSURE_PROFILE.profile_id,
}


def get_profile(profile_id: str | None) -> DiagnosisProfile | None:
    """Look up a profile by id."""
    if not profile_id:
        return None
    return PROFILE_REGISTRY.get(profile_id)


def supports_profile_execution(profile_id: str | None) -> bool:
    """Whether the current engine can execute this profile."""
    return bool(profile_id and profile_id in EXECUTABLE_PROFILE_IDS)


def infer_diagnosis_intent(
    *,
    mode: str,
    input_text: str,
    matched_skills: list[dict] | None = None,
) -> DiagnosisIntent:
    """Infer a coarse intent for state initialization and guardrails."""
    if mode == "default":
        return DiagnosisIntent.DEFAULT_PATROL

    normalized = (input_text or "").lower()
    remediation_tokens = (
        "怎么办",
        "如何处理",
        "处理建议",
        "清理建议",
        "how to",
        "what should",
        "fix",
        "cleanup",
    )
    status_tokens = (
        "现在",
        "当前",
        "情况如何",
        "status",
        "usage",
        "占用",
        "空间使用情况",
    )
    if any(token in normalized for token in remediation_tokens):
        return DiagnosisIntent.REMEDIATION_REQUEST
    if any(token in normalized for token in status_tokens):
        return DiagnosisIntent.STATUS_QUERY
    if matched_skills:
        return DiagnosisIntent.INCIDENT_DIAGNOSIS
    return DiagnosisIntent.KNOWLEDGE_ONLY


def resolve_selected_profile(
    *,
    mode: str,
    matched_skills: list[dict] | None = None,
) -> DiagnosisProfile | None:
    """Resolve the effective profile for the current request."""
    if mode == "default":
        return DEFAULT_PATROL_PROFILE

    for skill in matched_skills or []:
        if skill.get("skill_mode") != "execution_profile":
            continue
        profile = get_profile(skill.get("profile_id"))
        if profile:
            return profile
    return None
