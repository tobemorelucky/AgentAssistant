"""Profile registry and intent/profile selection helpers."""

from __future__ import annotations

from .models import DiagnosisIntent, DiagnosisProfile


HOST_HEALTH_PATROL_PROFILE = DiagnosisProfile(
    profile_id="host_health_patrol_profile",
    supported_intents=[DiagnosisIntent.DEFAULT_PATROL],
    resource_type="host_health",
    required_evidence_slots=["cpu_summary", "memory_summary", "disk_usage"],
    conditional_evidence_slots=["active_alerts"],
    reference_evidence_slots=[],
    stop_rules={
        "max_rounds": 2,
        "max_no_progress_rounds": 1,
        "max_attempts_per_slot": 2,
    },
    report_schema=[
        "巡检任务",
        "巡检结论",
        "CPU 状态",
        "内存状态",
        "磁盘状态",
        "活跃告警",
        "风险提示",
        "后续建议",
    ],
)

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
        "巡检输入",
        "活跃告警",
        "分发结果",
        "后续建议",
    ],
)

# Compatibility alias. Default patrol now means host health patrol.
DEFAULT_PATROL_PROFILE = HOST_HEALTH_PATROL_PROFILE

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
    HOST_HEALTH_PATROL_PROFILE.profile_id: HOST_HEALTH_PATROL_PROFILE,
    PATROL_DISPATCH_PROFILE.profile_id: PATROL_DISPATCH_PROFILE,
    DISK_PRESSURE_PROFILE.profile_id: DISK_PRESSURE_PROFILE,
    MEMORY_PRESSURE_PROFILE.profile_id: MEMORY_PRESSURE_PROFILE,
    CPU_PRESSURE_PROFILE.profile_id: CPU_PRESSURE_PROFILE,
}

EXECUTABLE_PROFILE_IDS = {
    HOST_HEALTH_PATROL_PROFILE.profile_id,
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
        "修复",
        "cleanup",
        "fix",
        "how to",
    )
    status_tokens = (
        "情况如何",
        "状态",
        "usage",
        "status",
        "cpu 情况",
        "cpu情况",
        "内存情况",
        "memory status",
        "磁盘情况",
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
        return HOST_HEALTH_PATROL_PROFILE

    for skill in matched_skills or []:
        if skill.get("skill_mode") != "execution_profile":
            continue
        profile = get_profile(skill.get("profile_id"))
        if profile:
            return profile
    return None
