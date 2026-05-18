"""Evidence-driven runtime helpers for host health patrol."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from app.agent.aiops.disk_cleanup import normalize_disk_tool_result, summarize_disk_tool_result

from .cpu_engine import normalize_cpu_tool_result, summarize_cpu_tool_result
from .evidence import record_evidence_attempt
from .memory_engine import normalize_memory_tool_result, summarize_memory_tool_result
from .models import EvidenceStatus, StopDecision, StopDecisionType


HOST_HEALTH_PATROL_PROFILE_ID = "host_health_patrol_profile"
REQUIRED_SLOT_ORDER = ["cpu_summary", "memory_summary", "disk_usage"]
OPTIONAL_SLOT_ORDER = ["active_alerts"]
SLOT_TOOL_MAP = {
    "cpu_summary": "get_cpu_summary",
    "memory_summary": "get_memory_summary",
    "disk_usage": "get_disk_usage",
    "active_alerts": "get_patrol_alerts",
}


def build_initial_host_health_tasks() -> list[dict[str, Any]]:
    return [
        {
            "slot": "cpu_summary",
            "tool": "get_cpu_summary",
            "args": {},
            "required": True,
            "reason": "获取主机实时 CPU 摘要，确认当前 CPU 健康状态。",
            "evidence_type": "cpu_summary",
        },
        {
            "slot": "memory_summary",
            "tool": "get_memory_summary",
            "args": {},
            "required": True,
            "reason": "获取主机实时内存摘要，确认当前内存健康状态。",
            "evidence_type": "memory_summary",
        },
        {
            "slot": "disk_usage",
            "tool": "get_disk_usage",
            "args": {"mount": "/"},
            "required": True,
            "reason": "获取主机根分区磁盘使用率，确认当前磁盘健康状态。",
            "evidence_type": "disk_usage",
        },
        {
            "slot": "active_alerts",
            "tool": "get_patrol_alerts",
            "args": {"include_resolved": False},
            "required": False,
            "reason": "查询当前主机级活跃告警，辅助判断是否存在 warning 或 critical 信号。",
            "evidence_type": "active_alerts",
        },
    ]


def normalize_host_health_tool_result(tool_name: str, raw_result: Any) -> dict[str, Any]:
    if tool_name == "get_cpu_summary":
        return normalize_cpu_tool_result(tool_name, raw_result)
    if tool_name == "get_memory_summary":
        return normalize_memory_tool_result(tool_name, raw_result)
    if tool_name == "get_disk_usage":
        result = normalize_disk_tool_result(tool_name, raw_result)
        return result if isinstance(result, dict) else {"ok": False, "message": "磁盘摘要格式无效", "source": "unknown"}
    if tool_name == "get_patrol_alerts":
        payload = dict(raw_result) if isinstance(raw_result, dict) else {}
        if payload.get("ok") is False:
            return {
                "ok": False,
                "source": payload.get("source") or "unknown",
                "message": str(payload.get("message") or payload.get("error") or "未成功获取活跃告警"),
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
    if slot == "cpu_summary":
        return normalized_result.get("usage_percent") is not None
    if slot == "memory_summary":
        return normalized_result.get("usage_percent") is not None
    if slot == "disk_usage":
        return normalized_result.get("usage_percent") is not None
    if slot == "active_alerts":
        return True
    return False


def _result_quality(slot: str, normalized_result: dict[str, Any]) -> tuple[EvidenceStatus, str, str]:
    if normalized_result.get("ok") is False:
        return EvidenceStatus.FAILED, "low", str(normalized_result.get("message") or "工具执行失败")
    if _slot_is_usable(slot, normalized_result):
        return EvidenceStatus.COLLECTED, "high" if slot != "active_alerts" else "medium", ""
    if slot == "active_alerts":
        return EvidenceStatus.PARTIAL, "low", "活跃告警为空"
    return EvidenceStatus.FAILED, "low", "未成功获取必需的主机健康证据"


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
        args = {"mount": "/"} if slot == "disk_usage" else {}
        follow_ups.append(
            {
                "slot": slot,
                "tool": SLOT_TOOL_MAP[slot],
                "args": args,
                "required": True,
                "reason": f"补查主机健康必需证据槽 {slot}。",
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
            reason="required host health evidence collected",
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
            reason="required host health evidence exhausted retries",
            missing_slots=missing_slots,
        )
    if investigation_round >= max_rounds:
        return StopDecision(
            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
            reason="host health patrol reached max rounds",
            missing_slots=missing_slots,
        )
    if no_progress_rounds >= max_no_progress_rounds:
        return StopDecision(
            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
            reason="host health patrol made no progress",
            missing_slots=missing_slots,
        )
    return StopDecision(
        decision=StopDecisionType.CONTINUE,
        reason="missing required host health evidence",
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
        return str(normalized_result.get("message") or f"{tool_name} 执行失败")
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
    return str(payload.get("status") or "unknown")


def _has_resource_issue(cpu: dict[str, Any], memory: dict[str, Any], disk: dict[str, Any]) -> bool:
    return any(_status_label(item) in {"warning", "critical"} for item in (cpu, memory, disk))


def build_host_health_patrol_report(state: dict[str, Any]) -> str:
    task_text = str(state.get("input") or "请开始一次 AIOps 巡检，并保留完整 Agent Trace。")
    cpu = _get_slot_payload(state, "cpu_summary")
    memory = _get_slot_payload(state, "memory_summary")
    disk = _get_slot_payload(state, "disk_usage")
    active_alerts_payload = _get_slot_payload(state, "active_alerts")
    active_alerts = list(active_alerts_payload.get("active_alerts") or [])

    host = _host_from_state(state)
    missing_slots = _required_missing_slots(state)

    if not missing_slots and not _has_resource_issue(cpu, memory, disk) and not active_alerts:
        conclusion = "- 当前未发现明显资源级异常。"
        conclusion += "\n- 当前无需执行处置动作。"
        conclusion += "\n- 未发现 warning / critical 主机级告警。"
    else:
        conclusion_lines: list[str] = []
        if _status_label(cpu) in {"warning", "critical"}:
            conclusion_lines.append("- 主机 CPU 出现压力信号，建议进入 CPU 专项诊断。")
        if _status_label(memory) in {"warning", "critical"}:
            conclusion_lines.append("- 主机内存出现压力信号，建议进入 Memory 专项诊断。")
        if _status_label(disk) in {"warning", "critical"}:
            conclusion_lines.append("- 主机磁盘出现压力信号，建议进入 Disk 专项诊断。")
        if active_alerts:
            conclusion_lines.append(f"- 当前主机级活跃告警数量为 `{len(active_alerts)}`。")
        if missing_slots:
            conclusion_lines.append("- 巡检存在证据缺口，以下结论仅基于已成功采集的实时数据。")
        conclusion = "\n".join(conclusion_lines or ["- 本次巡检已完成，但需要结合证据缺口继续判断。"])

    cpu_block = (
        f"- 状态：`{_status_label(cpu)}`\n"
        f"- 使用率：`{cpu.get('usage_percent') if cpu.get('usage_percent') is not None else '该字段未返回'}%`\n"
        f"- 负载：`load1={cpu.get('load_1')}` / `load5={cpu.get('load_5')}` / `load15={cpu.get('load_15')}`"
        if cpu.get("ok") is not False and cpu.get("usage_percent") is not None
        else f"- 未成功获取 CPU 实时状态：{cpu.get('message') or '该字段未返回'}"
    )
    memory_block = (
        f"- 状态：`{_status_label(memory)}`\n"
        f"- 使用率：`{memory.get('usage_percent') if memory.get('usage_percent') is not None else '该字段未返回'}%`\n"
        f"- 已用 / 总量：`{memory.get('used_gb')}GB / {memory.get('total_gb')}GB`"
        if memory.get("ok") is not False and memory.get("usage_percent") is not None
        else f"- 未成功获取内存实时状态：{memory.get('message') or '该字段未返回'}"
    )
    disk_block = (
        f"- 状态：`{_status_label(disk)}`\n"
        f"- 使用率：`{disk.get('usage_percent') if disk.get('usage_percent') is not None else '该字段未返回'}%`\n"
        f"- 已用 / 总量：`{disk.get('used_gb')}GB / {disk.get('total_gb')}GB`"
        if disk.get("ok") is not False and disk.get("usage_percent") is not None
        else f"- 未成功获取磁盘实时状态：{disk.get('message') or '该字段未返回'}"
    )

    if active_alerts_payload.get("ok") is False:
        alerts_block = f"- 未成功获取活跃告警：{active_alerts_payload.get('message') or '该字段未返回'}"
    elif active_alerts:
        alerts_block = "\n".join(
            f"- `{alert.get('alert_name')}` / `{alert.get('severity')}` / `{alert.get('host') or alert.get('service_name') or 'unknown-target'}`"
            for alert in active_alerts[:5]
            if isinstance(alert, dict)
        )
    else:
        alerts_block = "- 当前未发现活跃主机级告警。"

    gap_lines: list[str] = []
    if "cpu_summary" in missing_slots:
        gap_lines.append("- 未成功获取 CPU 实时摘要。")
    if "memory_summary" in missing_slots:
        gap_lines.append("- 未成功获取内存实时摘要。")
    if "disk_usage" in missing_slots:
        gap_lines.append("- 未成功获取磁盘实时摘要。")
    if not gap_lines:
        gap_lines.append("- 当前必需巡检证据已成功采集。")

    recommendation_lines = [
        "- 若 CPU 状态异常，建议继续执行 CPU 专项诊断。",
        "- 若内存状态异常，建议继续执行 Memory 专项诊断。",
        "- 若磁盘状态异常，建议继续执行 Disk 专项诊断。",
    ]
    if not _has_resource_issue(cpu, memory, disk):
        recommendation_lines.insert(0, "- 当前主机资源水位正常，可继续观察，无需立即处置。")

    warning_lines = [
        "- 本次巡检仅基于当前主机 CPU、内存、磁盘实时摘要和可选活跃告警。",
        "- 本轮未执行任何重启、清理、扩容或其他高风险操作。",
    ]

    return dedent(
        f"""
        # AIOps 主机健康巡检报告

        ## 巡检任务
        - 任务：{task_text}
        - 对象：`{host}`

        ## 巡检结论
        {conclusion}

        ## CPU 状态
        {cpu_block}

        ## 内存状态
        {memory_block}

        ## 磁盘状态
        {disk_block}

        ## 活跃告警
        {alerts_block}

        ## 证据缺口
        {chr(10).join(gap_lines)}

        ## 风险提示
        {chr(10).join(warning_lines)}

        ## 后续建议
        {chr(10).join(recommendation_lines)}
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
            findings.append(f"报告缺少必要章节：{section}")

    for slot in _required_missing_slots(state):
        missing.append(slot)
        slot_phrase = {
            "cpu_summary": "未成功获取 CPU 实时摘要",
            "memory_summary": "未成功获取内存实时摘要",
            "disk_usage": "未成功获取磁盘实时摘要",
        }[slot]
        if slot_phrase not in report:
            findings.append(f"报告未说明证据缺口：{slot}")

    if "本轮未执行任何重启、清理、扩容或其他高风险操作" not in report:
        warnings.append("风险提示中缺少未执行高风险操作说明。")

    return findings, missing, warnings
