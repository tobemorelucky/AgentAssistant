"""Evidence-driven runtime helpers for host health patrol."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from app.agent.aiops.disk_cleanup import normalize_disk_tool_result, summarize_disk_tool_result

from .cpu_engine import normalize_cpu_tool_result, summarize_cpu_tool_result
from .evidence import record_evidence_attempt
from .memory_engine import normalize_memory_tool_result, summarize_memory_tool_result
from .models import EvidenceStatus, StopDecision, StopDecisionType
from .profiles import get_profile


HOST_HEALTH_PATROL_PROFILE_ID = "host_health_patrol_profile"
REQUIRED_SLOT_ORDER = ["cpu_summary", "memory_summary", "disk_usage"]
OPTIONAL_SLOT_ORDER = ["active_alerts"]
SLOT_TOOL_MAP = {
    "cpu_summary": "get_cpu_summary",
    "memory_summary": "get_memory_summary",
    "disk_usage": "get_disk_usage",
    "active_alerts": "get_patrol_alerts",
}
RESOURCE_TO_PROFILE = {
    "cpu": "cpu_pressure_profile",
    "memory": "memory_pressure_profile",
    "disk": "disk_pressure_profile",
}
RESOURCE_TO_ALERT_NAME = {
    "cpu": "HostHighCPUUsage",
    "memory": "HostHighMemoryUsage",
    "disk": "HostHighDiskUsage",
}
SEVERITY_ORDER = {"critical": 4, "high": 3, "warning": 2, "medium": 1, "low": 0, "info": 0}


def _resolve_alert_helpers():
    from .patrol_dispatch import resolve_alert_profile_id, select_target_alert

    return resolve_alert_profile_id, select_target_alert


def build_initial_host_health_tasks() -> list[dict[str, Any]]:
    return [
        {
            "slot": "cpu_summary",
            "tool": "get_cpu_summary",
            "args": {},
            "required": True,
            "reason": "Collect the host CPU summary for the patrol baseline.",
            "evidence_type": "cpu_summary",
        },
        {
            "slot": "memory_summary",
            "tool": "get_memory_summary",
            "args": {},
            "required": True,
            "reason": "Collect the host memory summary for the patrol baseline.",
            "evidence_type": "memory_summary",
        },
        {
            "slot": "disk_usage",
            "tool": "get_disk_usage",
            "args": {"mount": "/"},
            "required": True,
            "reason": "Collect the primary disk usage baseline for the patrol.",
            "evidence_type": "disk_usage",
        },
        {
            "slot": "active_alerts",
            "tool": "get_patrol_alerts",
            "args": {"include_resolved": False},
            "required": False,
            "reason": "Collect active alerts to correlate host-health findings.",
            "evidence_type": "active_alerts",
        },
    ]


def _failure_result(message: str, *, source: str = "unknown", error_code: str = "tool_execution_error") -> dict[str, Any]:
    return {
        "ok": False,
        "source": source,
        "message": message,
        "error_code": error_code,
    }


def normalize_host_health_tool_result(tool_name: str, raw_result: Any) -> dict[str, Any]:
    if tool_name == "get_cpu_summary":
        return normalize_cpu_tool_result(tool_name, raw_result)
    if tool_name == "get_memory_summary":
        return normalize_memory_tool_result(tool_name, raw_result)
    if tool_name == "get_disk_usage":
        result = normalize_disk_tool_result(tool_name, raw_result)
        return result if isinstance(result, dict) else _failure_result("Invalid disk usage payload.")
    if tool_name == "get_patrol_alerts":
        payload = dict(raw_result) if isinstance(raw_result, dict) else {}
        if payload.get("ok") is False or payload.get("error") or payload.get("error_code"):
            return {
                "ok": False,
                "source": payload.get("source") or "unknown",
                "message": str(payload.get("message") or payload.get("error") or "Failed to fetch active alerts."),
                "error_code": str(payload.get("error_code") or "tool_execution_error"),
                "active_alerts": [],
            }
        active_alerts = list(payload.get("active_alerts") or payload.get("alerts") or [])
        return {
            "ok": True,
            "source": payload.get("source") or "unknown",
            "provider": payload.get("provider") or payload.get("source") or "unknown",
            "active_alerts": active_alerts,
            "message": str(payload.get("message") or ""),
        }
    return {"ok": True, "source": "unknown", "payload": raw_result}


def _slot_is_usable(slot: str, normalized_result: dict[str, Any]) -> bool:
    if normalized_result.get("ok") is False:
        return False
    if slot == "active_alerts":
        return True
    return normalized_result.get("usage_percent") is not None


def _result_quality(slot: str, normalized_result: dict[str, Any]) -> tuple[EvidenceStatus, str, str]:
    if normalized_result.get("ok") is False:
        return EvidenceStatus.FAILED, "low", str(normalized_result.get("message") or "Tool failed.")
    if _slot_is_usable(slot, normalized_result):
        return EvidenceStatus.COLLECTED, "high" if slot != "active_alerts" else "medium", ""
    if slot == "active_alerts":
        return EvidenceStatus.PARTIAL, "low", "No active alerts were returned."
    return EvidenceStatus.FAILED, "low", f"{slot} did not return usable patrol evidence."


def update_host_health_evidence_store(
    evidence_store: dict[str, dict[str, Any]],
    *,
    slot: str,
    tool_name: str,
    raw_result: Any,
) -> dict[str, dict[str, Any]]:
    normalized = normalize_host_health_tool_result(tool_name, raw_result)
    status, quality, error_message = _result_quality(slot, normalized)
    return record_evidence_attempt(
        evidence_store,
        slot=slot,
        status=status,
        source=str(normalized.get("source") or ""),
        payload=normalized,
        quality=quality,
        error_message=error_message,
    )


def _get_slot_record(state: dict[str, Any], slot: str) -> dict[str, Any]:
    evidence_store = dict(state.get("evidence_store") or {})
    payload = evidence_store.get(slot) or {}
    return payload if isinstance(payload, dict) else {}


def _get_slot_payload(state: dict[str, Any], slot: str) -> dict[str, Any]:
    record = _get_slot_record(state, slot)
    payload = record.get("payload") or {}
    return payload if isinstance(payload, dict) else {}


def _required_missing_slots(state: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for slot in REQUIRED_SLOT_ORDER:
        record = _get_slot_record(state, slot)
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if not isinstance(payload, dict) or not _slot_is_usable(slot, payload):
            missing.append(slot)
    return missing


def build_follow_up_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    follow_ups: list[dict[str, Any]] = []
    max_attempts = 2
    for slot in REQUIRED_SLOT_ORDER:
        record = _get_slot_record(state, slot)
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        attempts = int(record.get("attempts") or 0)
        if isinstance(payload, dict) and _slot_is_usable(slot, payload):
            continue
        if attempts >= max_attempts:
            continue
        follow_ups.append(
            {
                "slot": slot,
                "tool": SLOT_TOOL_MAP[slot],
                "args": {"mount": "/"} if slot == "disk_usage" else {},
                "required": True,
                "reason": f"Retry patrol evidence collection for {slot}.",
                "evidence_type": slot,
            }
        )
    return follow_ups


def compute_host_health_no_progress_rounds(
    evidence_store: dict[str, dict[str, Any]],
    *,
    previous_no_progress_rounds: int,
    last_slot: str | None,
) -> int:
    if not last_slot:
        return previous_no_progress_rounds
    record = evidence_store.get(last_slot) or {}
    if not isinstance(record, dict):
        return previous_no_progress_rounds + 1
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    if not isinstance(payload, dict) or not _slot_is_usable(last_slot, payload):
        return previous_no_progress_rounds + 1
    return 0


def decide_host_health_stop(state: dict[str, Any]) -> StopDecision:
    evidence_store = dict(state.get("evidence_store") or {})
    investigation_round = int(state.get("investigation_round") or 0)
    no_progress_rounds = int(state.get("no_progress_rounds") or 0)
    max_rounds = 2
    max_no_progress_rounds = 1
    max_attempts = 2

    missing_slots = _required_missing_slots(state)
    if not missing_slots:
        return StopDecision(
            decision=StopDecisionType.FINALIZE,
            reason="Required host-health evidence collected.",
            missing_slots=[],
        )

    exhausted = []
    for slot in missing_slots:
        record = evidence_store.get(slot) or {}
        if int(record.get("attempts") or 0) >= max_attempts:
            exhausted.append(slot)

    if exhausted:
        return StopDecision(
            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
            reason="Required host-health evidence exhausted retries.",
            missing_slots=missing_slots,
        )
    if investigation_round >= max_rounds:
        return StopDecision(
            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
            reason="Host-health patrol reached the maximum rounds.",
            missing_slots=missing_slots,
        )
    if no_progress_rounds >= max_no_progress_rounds:
        return StopDecision(
            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
            reason="Host-health patrol made no progress.",
            missing_slots=missing_slots,
        )
    return StopDecision(
        decision=StopDecisionType.CONTINUE,
        reason="Missing required host-health evidence.",
        missing_slots=missing_slots,
    )


def summarize_host_health_tool_result(tool_name: str, normalized_result: dict[str, Any]) -> str:
    if tool_name == "get_cpu_summary":
        return summarize_cpu_tool_result(tool_name, normalized_result)
    if tool_name == "get_memory_summary":
        return summarize_memory_tool_result(tool_name, normalized_result)
    if tool_name == "get_disk_usage":
        return summarize_disk_tool_result(tool_name, normalized_result)
    if normalized_result.get("ok") is False:
        return str(normalized_result.get("message") or f"{tool_name} failed")
    if tool_name == "get_patrol_alerts":
        alerts = normalized_result.get("active_alerts") or []
        return f"active_alerts={len(alerts)}"
    return str(normalized_result)


def summarize_host_health_investigation_task(task: dict[str, Any]) -> str:
    return f"{task.get('slot')} -> {task.get('tool')}"


def summarize_host_health_evidence_store(evidence_store: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for slot in REQUIRED_SLOT_ORDER + OPTIONAL_SLOT_ORDER:
        payload = evidence_store.get(slot) or {}
        if isinstance(payload, dict):
            parts.append(f"{slot}={payload.get('status')}#{payload.get('attempts', 0)}")
    return " | ".join(parts)


def _host_from_state(state: dict[str, Any]) -> str:
    for slot in ("cpu_summary", "memory_summary", "disk_usage"):
        payload = _get_slot_payload(state, slot)
        host = payload.get("host")
        if host:
            return str(host)
    alerts = _get_slot_payload(state, "active_alerts").get("active_alerts") or []
    if alerts and isinstance(alerts[0], dict) and alerts[0].get("host"):
        return str(alerts[0]["host"])
    return "unknown-host"


def _status_label(payload: dict[str, Any]) -> str:
    return str(payload.get("status") or "unknown").lower()


def _resource_finding(
    *,
    resource_type: str,
    payload: dict[str, Any],
    slot: str,
    host: str,
) -> dict[str, Any] | None:
    status = _status_label(payload)
    if status not in {"warning", "critical"}:
        return None
    usage_percent = payload.get("usage_percent")
    summary = {
        "cpu": f"主机 {host} CPU 使用率 {usage_percent}% ，当前状态 {status}",
        "memory": f"主机 {host} 内存使用率 {usage_percent}% ，当前状态 {status}",
        "disk": f"主机 {host} 磁盘使用率 {usage_percent}% ，当前状态 {status}",
    }[resource_type]
    return {
        "resource_type": resource_type,
        "severity": status,
        "summary": summary,
        "evidence_slot": slot,
        "alert_name": RESOURCE_TO_ALERT_NAME[resource_type],
        "service_name": None,
        "host": host,
        "candidate_profile_id": RESOURCE_TO_PROFILE[resource_type],
        "source": payload.get("source") or "unknown",
    }


def collect_abnormal_findings(state: dict[str, Any]) -> list[dict[str, Any]]:
    resolve_alert_profile_id, _ = _resolve_alert_helpers()
    findings: list[dict[str, Any]] = []
    host = _host_from_state(state)
    cpu = _get_slot_payload(state, "cpu_summary")
    memory = _get_slot_payload(state, "memory_summary")
    disk = _get_slot_payload(state, "disk_usage")
    active_alerts = list(_get_slot_payload(state, "active_alerts").get("active_alerts") or [])

    for resource_type, payload, slot in (
        ("cpu", cpu, "cpu_summary"),
        ("memory", memory, "memory_summary"),
        ("disk", disk, "disk_usage"),
    ):
        finding = _resource_finding(resource_type=resource_type, payload=payload, slot=slot, host=host)
        if finding:
            findings.append(finding)

    for alert in active_alerts:
        if not isinstance(alert, dict):
            continue
        profile_id = resolve_alert_profile_id(alert)
        if not profile_id:
            continue
        findings.append(
            {
                "resource_type": str(alert.get("resource_type") or profile_id.replace("_pressure_profile", "")),
                "severity": str(alert.get("severity") or "warning").lower(),
                "summary": str(alert.get("description") or alert.get("alert_name") or "Active alert detected"),
                "evidence_slot": "active_alerts",
                "alert_name": alert.get("alert_name"),
                "service_name": alert.get("service_name"),
                "host": alert.get("host") or host,
                "candidate_profile_id": profile_id,
                "source": alert.get("source") or "unknown",
            }
        )
    return findings


def _pick_escalation_target(state: dict[str, Any], abnormal_findings: list[dict[str, Any]]) -> dict[str, Any] | None:
    resolve_alert_profile_id, select_target_alert = _resolve_alert_helpers()
    active_alerts = list(_get_slot_payload(state, "active_alerts").get("active_alerts") or [])
    target_alert = select_target_alert(active_alerts)
    if target_alert:
        profile_id = resolve_alert_profile_id(target_alert)
        if profile_id:
            return {
                "profile_id": profile_id,
                "reason": (
                    f"基础巡检发现最高优先级告警 `{target_alert.get('alert_name')}` / "
                    f"`{target_alert.get('severity')}`，因此自动升级进入对应专项诊断。"
                ),
                "target_alert": target_alert,
            }

    ranked = sorted(
        abnormal_findings,
        key=lambda item: (
            -SEVERITY_ORDER.get(str(item.get("severity") or "").lower(), -1),
            {"disk": 3, "memory": 2, "cpu": 1}.get(str(item.get("resource_type") or ""), 0),
        ),
    )
    for finding in ranked:
        profile_id = str(finding.get("candidate_profile_id") or "")
        if not profile_id:
            continue
        return {
            "profile_id": profile_id,
            "reason": f"基础巡检发现 {finding.get('summary')}，因此自动升级进入对应专项诊断。",
            "target_alert": {
                "alert_name": finding.get("alert_name"),
                "severity": finding.get("severity"),
                "service_name": finding.get("service_name"),
                "host": finding.get("host"),
                "source": finding.get("source"),
                "resource_type": finding.get("resource_type"),
                "description": finding.get("summary"),
            },
        }
    return None


def build_escalation_payload(state: dict[str, Any]) -> dict[str, Any] | None:
    if _required_missing_slots(state):
        return None
    abnormal_findings = collect_abnormal_findings(state)
    if not abnormal_findings:
        return None
    target = _pick_escalation_target(state, abnormal_findings)
    if not target:
        return None
    profile = get_profile(target["profile_id"])
    if profile is None:
        return None
    profile_dict = profile.model_dump() if hasattr(profile, "model_dump") else profile.dict()
    return {
        "abnormal_findings": abnormal_findings,
        "selected_escalation_profile": profile_dict,
        "escalation_reason": str(target["reason"]),
        "target_alert": target["target_alert"],
    }


def build_host_health_patrol_report(state: dict[str, Any]) -> str:
    task_text = str(state.get("input") or "请开始一次 AIOps 巡检，并保留完整 Agent Trace。")
    cpu = _get_slot_payload(state, "cpu_summary")
    memory = _get_slot_payload(state, "memory_summary")
    disk = _get_slot_payload(state, "disk_usage")
    active_alerts_payload = _get_slot_payload(state, "active_alerts")
    active_alerts = list(active_alerts_payload.get("active_alerts") or [])
    host = _host_from_state(state)
    missing_slots = _required_missing_slots(state)
    abnormal_findings = collect_abnormal_findings(state)

    if not missing_slots and not abnormal_findings:
        conclusion_lines = [
            "- 当前主机未发现明显资源级异常。",
            "- 当前无需执行处置动作。",
            "- 未发现 warning / critical 主机级告警。",
        ]
    else:
        conclusion_lines = []
        if abnormal_findings:
            for finding in abnormal_findings[:4]:
                conclusion_lines.append(f"- {finding.get('summary')}")
        if missing_slots:
            conclusion_lines.append("- 本轮巡检存在证据缺口，以下结论基于已成功采集的实时数据。")

    def _resource_block(name: str, payload: dict[str, Any]) -> str:
        if payload.get("ok") is False:
            return f"- 未成功获取{name}摘要：{payload.get('message') or '工具调用失败'}"
        if payload.get("usage_percent") is None:
            return f"- 未成功获取{name}摘要。"
        lines = [
            f"- 状态：`{payload.get('status') or 'unknown'}`",
            f"- 使用率：`{payload.get('usage_percent')}%`",
        ]
        if name == "CPU":
            lines.append(
                f"- 负载：`load1={payload.get('load_1')}` / `load5={payload.get('load_5')}` / `load15={payload.get('load_15')}`"
            )
        else:
            lines.append(
                f"- 已用 / 总量 / 可用：`{payload.get('used_gb')}GB / {payload.get('total_gb')}GB / {payload.get('available_gb')}GB`"
            )
        return "\n".join(lines)

    if active_alerts_payload.get("ok") is False:
        alerts_block = f"- 未成功获取活跃告警：{active_alerts_payload.get('message') or '工具调用失败'}"
    elif active_alerts:
        alerts_block = "\n".join(
            f"- `{alert.get('alert_name')}` / `{alert.get('severity')}` / "
            f"`{alert.get('host') or alert.get('service_name') or 'unknown-target'}`"
            for alert in active_alerts[:5]
            if isinstance(alert, dict)
        )
    else:
        alerts_block = "- 当前未发现活跃告警。"

    risk_lines = [
        "- 巡检仅覆盖主机 CPU、内存、磁盘和活跃告警摘要，未自动进入服务级日志或工单深查。",
        "- 本轮未执行任何重启、扩容、限流或其他高风险操作。",
    ]

    if abnormal_findings:
        next_steps = []
        seen_resources: set[str] = set()
        for finding in abnormal_findings:
            resource = str(finding.get("resource_type") or "")
            if resource in seen_resources:
                continue
            seen_resources.add(resource)
            if resource == "cpu":
                next_steps.append("- 建议进入 CPU 专项诊断，进一步确认热点进程、服务上下文和处理建议。")
            elif resource == "memory":
                next_steps.append("- 建议进入 Memory 专项诊断，进一步确认热点内存进程和缓解策略。")
            elif resource == "disk":
                next_steps.append("- 建议进入 Disk 专项诊断，进一步确认目录、大文件和回收候选项。")
    else:
        next_steps = ["- 建议持续观察主机资源趋势，必要时再次发起巡检。"]

    gap_lines = []
    for slot, label in (("cpu_summary", "CPU 摘要"), ("memory_summary", "内存摘要"), ("disk_usage", "磁盘摘要")):
        if slot in missing_slots:
            gap_lines.append(f"- 未成功获取{label}。")
    if not gap_lines:
        gap_lines.append("- 当前关键巡检证据已覆盖主机健康巡检首轮范围。")

    return dedent(
        f"""
        # AIOps 主机健康巡检报告

        ## 巡检任务
        - {task_text}
        - 主机：`{host}`

        ## 巡检结论
        {chr(10).join(conclusion_lines)}

        ## CPU 状态
        {_resource_block("CPU", cpu)}

        ## 内存状态
        {_resource_block("内存", memory)}

        ## 磁盘状态
        {_resource_block("磁盘", disk)}

        ## 活跃告警
        {alerts_block}

        ## 风险提示
        {chr(10).join(risk_lines)}

        ## 后续建议
        {chr(10).join(next_steps)}

        ## 证据缺口
        {chr(10).join(gap_lines)}
        """
    ).strip()


def verify_host_health_patrol_report(state: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    report = str(state.get("response") or "").strip()
    findings: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []

    required_sections = [
        "## 巡检结论",
        "## CPU 状态",
        "## 内存状态",
        "## 磁盘状态",
        "## 活跃告警",
        "## 风险提示",
        "## 后续建议",
    ]
    for section in required_sections:
        if section not in report:
            findings.append(f"报告缺少必需章节：{section}")

    for slot in _required_missing_slots(state):
        missing.append(slot)
        expected_phrase = {
            "cpu_summary": "未成功获取CPU 摘要",
            "memory_summary": "未成功获取内存摘要",
            "disk_usage": "未成功获取磁盘摘要",
        }.get(slot)
        if expected_phrase and expected_phrase not in report:
            findings.append(f"报告未对证据缺口作出说明：{slot}")

    if "本轮未执行任何重启、扩容、限流或其他高风险操作。" not in report:
        warnings.append("报告缺少巡检安全边界说明。")

    return findings, missing, warnings
