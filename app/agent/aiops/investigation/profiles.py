"""Profile registry and intent/profile selection helpers."""

from __future__ import annotations

from .models import DiagnosisIntent, DiagnosisProfile


PATROL_DISPATCH_PROFILE = DiagnosisProfile(
    profile_id="patrol_dispatch_profile",
    supported_intents=[DiagnosisIntent.DEFAULT_PATROL],
    resource_type="alert_triage",
    required_evidence_slots=["active_alerts", "target_alert"],
    conditional_evidence_slots=[],
    reference_evidence_slots=[],
    stop_rules={
        "max_rounds": 1,
        "max_no_progress_rounds": 0,
        "max_attempts_per_slot": 1,
    },
    report_schema=[
        "巡检摘要",
        "活跃告警",
        "分发结果",
        "后续建议",
    ],
)

# Compatibility alias. Phase 5 can remove this name after all legacy imports are gone.
DEFAULT_PATROL_PROFILE = PATROL_DISPATCH_PROFILE

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

MEMORY_PRESSURE_PROFILE = DiagnosisProfile(
    profile_id="memory_pressure_profile",
    supported_intents=[
        DiagnosisIntent.STATUS_QUERY,
        DiagnosisIntent.INCIDENT_DIAGNOSIS,
        DiagnosisIntent.REMEDIATION_REQUEST,
    ],
    resource_type="memory",
    required_evidence_slots=["memory_summary", "top_memory_processes"],
    conditional_evidence_slots=["memory_runbook"],
    reference_evidence_slots=["memory_runbook"],
    stop_rules={
        "max_rounds": 3,
        "max_no_progress_rounds": 1,
        "max_attempts_per_slot": 2,
    },
    report_schema=[
        "任务与对象",
        "已确认事实",
        "当前内存状态",
        "主要内存消耗来源",
        "候选风险 / 待验证解释",
        "证据缺口",
        "处理建议",
        "风险提示",
        "Runbook 参考",
    ],
)

CPU_PRESSURE_PROFILE = DiagnosisProfile(
    profile_id="cpu_pressure_profile",
    supported_intents=[
        DiagnosisIntent.STATUS_QUERY,
        DiagnosisIntent.INCIDENT_DIAGNOSIS,
        DiagnosisIntent.REMEDIATION_REQUEST,
    ],
    resource_type="cpu",
    required_evidence_slots=["cpu_summary", "top_cpu_processes"],
    conditional_evidence_slots=["cpu_runbook"],
    reference_evidence_slots=["cpu_runbook"],
    stop_rules={
        "max_rounds": 3,
        "max_no_progress_rounds": 1,
        "max_attempts_per_slot": 2,
    },
    report_schema=[
        "任务与对象",
        "已确认事实",
        "当前 CPU 状态",
        "主要 CPU 消耗来源",
        "候选风险 / 待验证解释",
        "证据缺口",
        "处理建议",
        "风险提示",
        "Runbook 参考",
    ],
)

PROFILE_REGISTRY: dict[str, DiagnosisProfile] = {
    PATROL_DISPATCH_PROFILE.profile_id: PATROL_DISPATCH_PROFILE,
    DISK_PRESSURE_PROFILE.profile_id: DISK_PRESSURE_PROFILE,
    MEMORY_PRESSURE_PROFILE.profile_id: MEMORY_PRESSURE_PROFILE,
    CPU_PRESSURE_PROFILE.profile_id: CPU_PRESSURE_PROFILE,
}

EXECUTABLE_PROFILE_IDS = {
    PATROL_DISPATCH_PROFILE.profile_id,
    DISK_PRESSURE_PROFILE.profile_id,
    MEMORY_PRESSURE_PROFILE.profile_id,
    CPU_PRESSURE_PROFILE.profile_id,
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
        "怎么处理",
        "处理建议",
        "如何处理",
        "cleanup",
        "fix",
        "how to",
    )
    status_tokens = (
        "现在",
        "当前",
        "情况如何",
        "usage",
        "status",
        "磁盘空间",
        "磁盘使用",
        "cpu 情况",
        "cpu情况",
        "内存情况",
        "memory status",
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
        return PATROL_DISPATCH_PROFILE

    for skill in matched_skills or []:
        if skill.get("skill_mode") != "execution_profile":
            continue
        profile = get_profile(skill.get("profile_id"))
        if profile:
            return profile
    return None
