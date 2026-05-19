"""Evidence-driven runtime helpers for CPU diagnosis."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from .evidence import record_evidence_attempt
from .models import EvidenceStatus, StopDecision, StopDecisionType
from ..utils import normalize_external_reference_result


CPU_PRESSURE_PROFILE_ID = "cpu_pressure_profile"
CPU_RUNBOOK_QUERY = "CPU 使用率过高 排查 runbook"
REQUIRED_SLOT_ORDER = ["cpu_summary", "top_cpu_processes"]
REFERENCE_SLOT_ORDER = ["cpu_runbook"]
CONDITIONAL_SLOT_ORDER = ["service_context", "historical_tickets", "external_reference"]
SLOT_TOOL_MAP = {
    "cpu_summary": "get_cpu_summary",
    "top_cpu_processes": "list_top_cpu_processes",
    "cpu_runbook": "retrieve_knowledge",
    "service_context": "get_service_info",
    "historical_tickets": "search_historical_tickets",
    "external_reference": "web_search",
}


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status_from_usage(usage_percent: float | None) -> str:
    if usage_percent is None:
        return "unknown"
    if usage_percent >= 90:
        return "critical"
    if usage_percent >= 80:
        return "warning"
    return "healthy"


def _failure_result(message: str, *, error_code: str, source: str = "unknown") -> dict[str, Any]:
    return {
        "ok": False,
        "source": source or "unknown",
        "message": message,
        "error_code": error_code,
    }


def _is_error_payload(payload: dict[str, Any]) -> bool:
    if not payload:
        return True
    if payload.get("ok") is False:
        return True
    if payload.get("error"):
        return True
    if payload.get("error_code"):
        return True
    return False


def _cpu_summary_is_usable(normalized_result: dict[str, Any]) -> bool:
    if normalized_result.get("ok") is False:
        return False
    usage_percent = normalized_result.get("usage_percent")
    host = str(normalized_result.get("host") or "")
    has_load = any(normalized_result.get(key) is not None for key in ("load_1", "load_5", "load_15"))
    has_cores = normalized_result.get("cores") is not None
    return usage_percent is not None or (host not in {"", "unknown-host"} and (has_load or has_cores))


def _top_cpu_processes_is_usable(normalized_result: dict[str, Any]) -> bool:
    if normalized_result.get("ok") is False:
        return False
    processes = normalized_result.get("processes")
    return isinstance(processes, list) and len(processes) > 0


def _slot_is_usable(slot: str, normalized_result: dict[str, Any]) -> bool:
    if slot == "cpu_summary":
        return _cpu_summary_is_usable(normalized_result)
    if slot == "top_cpu_processes":
        return _top_cpu_processes_is_usable(normalized_result)
    if slot == "cpu_runbook":
        return bool(str(normalized_result.get("content") or "").strip())
    if slot == "service_context":
        return bool(str(normalized_result.get("service_name") or "").strip())
    if slot == "historical_tickets":
        return isinstance(normalized_result.get("tickets"), list)
    if slot == "external_reference":
        return bool(str(normalized_result.get("content") or "").strip())
    return normalized_result.get("ok") is not False


def build_initial_cpu_tasks() -> list[dict[str, Any]]:
    return [
        {
            "slot": "cpu_summary",
            "tool": "get_cpu_summary",
            "args": {},
            "required": True,
            "reason": "Collect the real-time CPU summary for the target host.",
            "evidence_type": "cpu_summary",
        },
        {
            "slot": "top_cpu_processes",
            "tool": "list_top_cpu_processes",
            "args": {"limit": 10},
            "required": True,
            "reason": "Collect the hottest CPU processes to identify current pressure sources.",
            "evidence_type": "top_cpu_processes",
        },
        {
            "slot": "cpu_runbook",
            "tool": "retrieve_knowledge",
            "args": {"query": CPU_RUNBOOK_QUERY},
            "required": False,
            "reason": "Retrieve the local CPU troubleshooting runbook from the knowledge base.",
            "evidence_type": "cpu_runbook",
        },
    ]


def normalize_cpu_tool_result(tool_name: str, raw_result: Any) -> dict[str, Any]:
    payload = dict(raw_result) if isinstance(raw_result, dict) else {}

    if tool_name == "get_cpu_summary":
        if _is_error_payload(payload):
            return _failure_result(
                str(payload.get("message") or payload.get("error") or "Failed to get CPU summary."),
                error_code=str(payload.get("error_code") or "tool_execution_error"),
                source=str(payload.get("source") or "unknown"),
            )
        usage_percent = _to_float(payload.get("usage_percent"))
        if usage_percent is None:
            usage_percent = _to_float(payload.get("cpu_percent"))
        cores = payload.get("cores")
        if cores is None:
            cores = payload.get("logical_cpu_count")
        result = {
            "ok": True,
            "host": payload.get("host") or payload.get("hostname") or "unknown-host",
            "usage_percent": usage_percent,
            "cores": cores,
            "logical_cpu_count": payload.get("logical_cpu_count") or cores,
            "load_1": _to_float(payload.get("load_1") if payload.get("load_1") is not None else payload.get("load_1m")),
            "load_5": _to_float(payload.get("load_5") if payload.get("load_5") is not None else payload.get("load_5m")),
            "load_15": _to_float(payload.get("load_15") if payload.get("load_15") is not None else payload.get("load_15m")),
            "status": payload.get("status") or _status_from_usage(usage_percent),
            "source": payload.get("source") or "unknown",
        }
        if not _cpu_summary_is_usable(result):
            return _failure_result(
                "CPU summary did not contain usable real-time fields.",
                error_code="invalid_cpu_summary",
                source=str(result.get("source") or "unknown"),
            )
        return result

    if tool_name == "list_top_cpu_processes":
        if _is_error_payload(payload):
            return _failure_result(
                str(payload.get("message") or payload.get("error") or "Failed to list top CPU processes."),
                error_code=str(payload.get("error_code") or "tool_execution_error"),
                source=str(payload.get("source") or "unknown"),
            )
        processes_raw = payload.get("processes")
        if not isinstance(processes_raw, list):
            processes_raw = raw_result if isinstance(raw_result, list) else []
        processes: list[dict[str, Any]] = []
        for item in processes_raw:
            if not isinstance(item, dict):
                continue
            cpu_percent = _to_float(item.get("cpu_percent"))
            if cpu_percent is None:
                cpu_percent = _to_float(item.get("usage_percent"))
            processes.append(
                {
                    "pid": item.get("pid"),
                    "process_name": item.get("process_name") or item.get("name") or item.get("command") or "unknown",
                    "command": item.get("command") or "",
                    "cpu_percent": cpu_percent,
                    "threads": item.get("threads"),
                    "source": item.get("source") or payload.get("source") or "unknown",
                }
            )
        return {
            "ok": True,
            "processes": processes,
            "limit": int(payload.get("limit") or len(processes) or 0),
            "message": "" if processes else "No top CPU processes were returned.",
            "source": payload.get("source") or "unknown",
        }

    if tool_name == "retrieve_knowledge":
        if isinstance(raw_result, dict):
            content = str(raw_result.get("content") or raw_result.get("answer") or "").strip()
            return {
                "ok": bool(content),
                "content": content,
                "source": raw_result.get("source") or "local_knowledge",
            }
        text = str(raw_result or "").strip()
        return {"ok": bool(text), "content": text, "source": "local_knowledge"}

    if tool_name == "get_service_info":
        if _is_error_payload(payload):
            return _failure_result(
                str(payload.get("message") or payload.get("error") or "Failed to get service info."),
                error_code=str(payload.get("error_code") or "tool_execution_error"),
                source=str(payload.get("source") or "unknown"),
            )
        return {
            "ok": True,
            "service_name": payload.get("service_name"),
            "owner_team": payload.get("owner_team"),
            "deployment": payload.get("deployment"),
            "dependencies": payload.get("dependencies") or [],
            "source": payload.get("source") or "unknown",
        }

    if tool_name == "search_historical_tickets":
        if _is_error_payload(payload):
            return _failure_result(
                str(payload.get("message") or payload.get("error") or "Failed to query historical tickets."),
                error_code=str(payload.get("error_code") or "tool_execution_error"),
                source=str(payload.get("source") or "unknown"),
            )
        tickets = payload.get("tickets")
        if not isinstance(tickets, list):
            tickets = []
        return {
            "ok": True,
            "tickets": tickets,
            "total": payload.get("total") or len(tickets),
            "source": payload.get("source") or "unknown",
        }

    if tool_name == "web_search":
        return normalize_external_reference_result(raw_result)

    return {"ok": True, "source": "unknown", "payload": raw_result}


def _result_quality(slot: str, normalized_result: dict[str, Any]) -> tuple[EvidenceStatus, str, str]:
    if normalized_result.get("ok") is False:
        return EvidenceStatus.FAILED, "low", str(normalized_result.get("message") or "Tool failed.")

    if slot == "cpu_summary":
        if _cpu_summary_is_usable(normalized_result):
            if normalized_result.get("usage_percent") is not None:
                return EvidenceStatus.COLLECTED, "high", ""
            return EvidenceStatus.PARTIAL, "medium", "CPU summary is present but usage_percent is missing."
        return EvidenceStatus.FAILED, "low", "CPU summary is not usable."

    if slot == "top_cpu_processes":
        if _top_cpu_processes_is_usable(normalized_result):
            return EvidenceStatus.COLLECTED, "high", ""
        return EvidenceStatus.FAILED, "low", "Top CPU process list is missing."

    if slot == "cpu_runbook":
        if normalized_result.get("content"):
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "Local CPU runbook returned empty content."

    if slot == "service_context":
        if normalized_result.get("service_name"):
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "Service context was not returned."

    if slot == "historical_tickets":
        if isinstance(normalized_result.get("tickets"), list):
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "Historical tickets were not returned."

    if slot == "external_reference":
        if normalized_result.get("content"):
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "External search returned no usable content."

    return EvidenceStatus.PARTIAL, "unknown", ""


def update_cpu_evidence_store(
    evidence_store: dict[str, dict[str, Any]],
    *,
    slot: str,
    tool_name: str,
    raw_result: Any,
) -> dict[str, dict[str, Any]]:
    normalized = normalize_cpu_tool_result(tool_name, raw_result)
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
    return _get_slot_record(state, slot).get("payload") or {}


def _required_missing_slots(state: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for slot in REQUIRED_SLOT_ORDER:
        record = _get_slot_record(state, slot)
        status = str(record.get("status") or EvidenceStatus.MISSING)
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if status != EvidenceStatus.COLLECTED:
            if not isinstance(payload, dict) or not _slot_is_usable(slot, payload):
                missing.append(slot)
    return missing


def _build_external_search_query(state: dict[str, Any]) -> str:
    target_alert = state.get("target_alert") or {}
    service_name = str(target_alert.get("service_name") or "")
    alert_name = str(target_alert.get("alert_name") or "HighCPUUsage")
    host = str(_get_slot_payload(state, "cpu_summary").get("host") or target_alert.get("host") or "linux host")
    if service_name:
        return f"Linux high CPU usage {service_name} {alert_name} troubleshooting"
    return f"Linux high CPU usage {host} troubleshooting best practice"


def _should_collect_service_context(state: dict[str, Any], slot: str) -> bool:
    target_alert = state.get("target_alert") or {}
    service_name = str(target_alert.get("service_name") or "")
    if not service_name:
        return False
    record = _get_slot_record(state, slot)
    return int(record.get("attempts") or 0) == 0


def build_follow_up_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    follow_ups: list[dict[str, Any]] = []
    max_attempts = 2

    for slot in REQUIRED_SLOT_ORDER:
        record = _get_slot_record(state, slot)
        attempts = int(record.get("attempts") or 0)
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if isinstance(payload, dict) and _slot_is_usable(slot, payload):
            continue
        if attempts >= max_attempts:
            continue
        follow_ups.append(
            {
                "slot": slot,
                "tool": SLOT_TOOL_MAP[slot],
                "args": {"limit": 10} if slot == "top_cpu_processes" else {},
                "required": True,
                "reason": f"Retry CPU evidence collection for {slot}.",
                "evidence_type": slot,
            }
        )

    if follow_ups:
        return follow_ups

    if _should_collect_service_context(state, "service_context"):
        service_name = str((state.get("target_alert") or {}).get("service_name"))
        follow_ups.append(
            {
                "slot": "service_context",
                "tool": "get_service_info",
                "args": {"service_name": service_name},
                "required": False,
                "reason": "Collect local service context because the alert contains a service_name.",
                "evidence_type": "service_context",
            }
        )
        follow_ups.append(
            {
                "slot": "historical_tickets",
                "tool": "search_historical_tickets",
                "args": {
                    "service_name": service_name,
                    "alert_name": (state.get("target_alert") or {}).get("alert_name"),
                    "limit": 5,
                },
                "required": False,
                "reason": "Collect local historical tickets for the alerted service.",
                "evidence_type": "historical_tickets",
            }
        )

    runbook_payload = _get_slot_payload(state, "cpu_runbook")
    external_reference_record = _get_slot_record(state, "external_reference")
    allow_external = bool(state.get("remediation_feedback_failed")) or not str(runbook_payload.get("content") or "").strip()
    if allow_external and int(external_reference_record.get("attempts") or 0) == 0:
        follow_ups.append(
            {
                "slot": "external_reference",
                "tool": "web_search",
                "args": {"query": _build_external_search_query(state)},
                "required": False,
                "reason": "Local runbook is insufficient or the user reported the previous guidance was ineffective.",
                "evidence_type": "external_reference",
            }
        )

    return follow_ups


def compute_cpu_no_progress_rounds(
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


def decide_cpu_stop(state: dict[str, Any]) -> StopDecision:
    evidence_store = dict(state.get("evidence_store") or {})
    investigation_round = int(state.get("investigation_round") or 0)
    no_progress_rounds = int(state.get("no_progress_rounds") or 0)
    max_rounds = 3
    max_no_progress_rounds = 1
    max_attempts = 2

    missing_slots = _required_missing_slots(state)
    if missing_slots:
        exhausted_slots = []
        for slot in missing_slots:
            record = evidence_store.get(slot) or {}
            attempts = int(record.get("attempts") or 0)
            if attempts >= max_attempts:
                exhausted_slots.append(slot)
        if exhausted_slots:
            return StopDecision(
                decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                reason="Required CPU evidence exhausted retries.",
                missing_slots=missing_slots,
            )
        if investigation_round >= max_rounds:
            return StopDecision(
                decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                reason="CPU investigation reached max rounds.",
                missing_slots=missing_slots,
            )
        if no_progress_rounds >= max_no_progress_rounds:
            return StopDecision(
                decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                reason="CPU investigation made no progress.",
                missing_slots=missing_slots,
            )
        return StopDecision(
            decision=StopDecisionType.CONTINUE,
            reason="Missing required CPU evidence.",
            missing_slots=missing_slots,
        )

    if build_follow_up_tasks(state):
        return StopDecision(
            decision=StopDecisionType.CONTINUE,
            reason="Collecting optional CPU evidence and references.",
            missing_slots=[],
        )

    return StopDecision(
        decision=StopDecisionType.FINALIZE,
        reason="CPU evidence collection is sufficient to finalize.",
        missing_slots=[],
    )


def summarize_cpu_tool_result(tool_name: str, normalized_result: dict[str, Any]) -> str:
    if normalized_result.get("ok") is False:
        return str(normalized_result.get("message") or f"{tool_name} failed")
    if tool_name == "get_cpu_summary":
        return (
            f"host={normalized_result.get('host')}, "
            f"usage={normalized_result.get('usage_percent')}%, "
            f"load1={normalized_result.get('load_1')}, load5={normalized_result.get('load_5')}"
        )
    if tool_name == "list_top_cpu_processes":
        processes = normalized_result.get("processes") or []
        top_parts = []
        for item in processes[:3]:
            if not isinstance(item, dict):
                continue
            top_parts.append(f"{item.get('process_name')} pid={item.get('pid')} cpu={item.get('cpu_percent')}%")
        return " | ".join(top_parts) if top_parts else "No top CPU processes were returned."
    if tool_name == "retrieve_knowledge":
        content = str(normalized_result.get("content") or "").strip()
        return content[:160] if content else "No local CPU runbook content was returned."
    if tool_name == "web_search":
        content = str(normalized_result.get("content") or "").strip()
        return content[:160] if content else "No external reference was returned."
    if tool_name == "get_service_info":
        return f"service={normalized_result.get('service_name')} owner={normalized_result.get('owner_team')}"
    if tool_name == "search_historical_tickets":
        tickets = normalized_result.get("tickets") or []
        return f"historical_tickets={len(tickets)}"
    return str(normalized_result)


def summarize_cpu_investigation_task(task: dict[str, Any]) -> str:
    slot = str(task.get("slot") or "")
    tool = str(task.get("tool") or "")
    return f"{slot} -> {tool}"


def summarize_cpu_evidence_store(evidence_store: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for slot in REQUIRED_SLOT_ORDER + REFERENCE_SLOT_ORDER + CONDITIONAL_SLOT_ORDER:
        payload = evidence_store.get(slot) or {}
        if not isinstance(payload, dict):
            continue
        parts.append(f"{slot}={payload.get('status')}#{payload.get('attempts', 0)}")
    return " | ".join(parts)


def _escalation_summary(state: dict[str, Any]) -> list[str]:
    profile = state.get("selected_escalation_profile") or {}
    reason = str(state.get("escalation_reason") or "").strip()
    target_alert = state.get("target_alert") or {}
    if not profile and not reason:
        return []
    lines = []
    if target_alert:
        lines.append(
            f"- 本轮基础巡检发现 `{target_alert.get('alert_name', 'unknown-alert')}` / "
            f"`{target_alert.get('severity', 'unknown')}`，系统自动升级进入 CPU 专项诊断。"
        )
    if reason:
        lines.append(f"- 升级原因：{reason}")
    return lines


def _render_external_reference(payload: dict[str, Any]) -> str:
    content = str(payload.get("content") or "").strip()
    artifacts = payload.get("artifacts") or []
    if not content and not artifacts:
        return "- 未获取到外部补充参考。"
    lines = []
    for artifact in artifacts[:3]:
        if not isinstance(artifact, dict):
            continue
        metadata = artifact.get("metadata") or {}
        lines.append(
            "- 标题：{title}\n"
            "  链接：{url}\n"
            "  摘要：{summary}\n"
            "  用途：用于补充外部公开处理思路，不属于本地主机实时证据。".format(
                title=metadata.get("title") or "未命名资料",
                url=metadata.get("source") or "未提供链接",
                summary=str(artifact.get("page_content") or "").replace("\n", " ")[:220],
            )
        )
    if not lines and content:
        lines.append(f"- 摘要：{content[:260]}")
    return "\n".join(lines)


def build_cpu_investigation_report(state: dict[str, Any]) -> str:
    task_text = str(state.get("input") or "系统现在 CPU 情况如何？")
    summary_payload = _get_slot_payload(state, "cpu_summary")
    process_payload = _get_slot_payload(state, "top_cpu_processes")
    runbook_payload = _get_slot_payload(state, "cpu_runbook")
    external_payload = _get_slot_payload(state, "external_reference")
    service_payload = _get_slot_payload(state, "service_context")
    tickets_payload = _get_slot_payload(state, "historical_tickets")
    host_health_evidence = state.get("host_health_evidence") or {}

    host = summary_payload.get("host") or (host_health_evidence.get("cpu_summary", {}).get("payload", {}) or {}).get("host") or "unknown-host"
    usage_percent = summary_payload.get("usage_percent")
    load_1 = summary_payload.get("load_1")
    load_5 = summary_payload.get("load_5")
    load_15 = summary_payload.get("load_15")
    status = summary_payload.get("status") or "unknown"

    facts: list[str] = []
    if _cpu_summary_is_usable(summary_payload):
        facts.append(f"- 主机 `{host}` 当前 CPU 使用率为 `{usage_percent}%`。")
        facts.append(f"- CPU 状态判定为 `{status}`。")
        if any(value is not None for value in (load_1, load_5, load_15)):
            facts.append(f"- 负载：`load1={load_1}` / `load5={load_5}` / `load15={load_15}`。")
    else:
        facts.append("- 未成功获取实时 CPU 摘要。")

    processes = process_payload.get("processes") if isinstance(process_payload, dict) else []
    process_lines: list[str] = []
    if isinstance(processes, list) and processes:
        for item in processes[:5]:
            if not isinstance(item, dict):
                continue
            process_lines.append(
                f"- `{item.get('process_name')}` (pid={item.get('pid')}) CPU `{item.get('cpu_percent')}%`"
            )
    else:
        process_lines.append("- 未成功获取热点 CPU 进程列表。")

    context_lines: list[str] = []
    if service_payload.get("service_name"):
        context_lines.append(
            f"- 服务：`{service_payload.get('service_name')}`，Owner：`{service_payload.get('owner_team') or 'unknown'}`"
        )
    tickets = tickets_payload.get("tickets") or []
    if isinstance(tickets, list) and tickets:
        first_ticket = tickets[0] if isinstance(tickets[0], dict) else {}
        context_lines.append(
            f"- 历史工单 `{first_ticket.get('ticket_id', 'unknown')}`：{first_ticket.get('root_cause', '未提供根因摘要')}"
        )
    if not context_lines:
        context_lines.append("- 当前没有可用的服务级上下文证据。")

    runbook_content = str(runbook_payload.get("content") or "").strip()
    runbook_lines = (
        [f"- 本地 Runbook 摘要：{runbook_content[:260]}"]
        if runbook_content
        else ["- 本地知识库未返回有效 CPU 处置方案。"]
    )

    risk_lines: list[str] = []
    if usage_percent is not None and usage_percent >= 85:
        risk_lines.append("- CPU 压力较高，可能导致请求延迟上升、队列积压或调度抖动。")
    elif usage_percent is not None:
        risk_lines.append("- 当前已观察到 CPU 压力信号，但仍需结合业务流量持续观察。")
    else:
        risk_lines.append("- 当前无法确认 CPU 压力强度，因为实时摘要未成功获取。")

    gap_lines: list[str] = []
    missing_slots = _required_missing_slots(state)
    if "cpu_summary" in missing_slots:
        gap_lines.append("- 未成功获取实时 CPU 摘要。")
    if "top_cpu_processes" in missing_slots:
        gap_lines.append("- 未成功获取热点 CPU 进程列表。")
    if not runbook_content:
        gap_lines.append("- 本地 Runbook / RAG 尚未给出有效处置方案。")
    if external_payload.get("ok") is False:
        gap_lines.append(f"- 外部补充参考获取失败：{external_payload.get('message') or 'web_search failed'}")
    if not gap_lines:
        gap_lines.append("- 当前关键证据已覆盖第一版 CPU Profile 所需范围。")

    recommendation_lines = [
        "- 继续观察 CPU 使用率、负载与热点进程变化，确认压力是否持续。",
        "- 如定位到单一高 CPU 进程，优先结合服务配置、任务并发度和调用路径做进一步排查。",
        "- 若服务级上下文存在，可继续补充日志、工单或应用侧指标进行深挖。",
    ]

    remediation_lines = [
        "### 可直接给出的低风险建议\n"
        "- 继续观察 CPU 曲线与热点进程变化。\n"
        "- 核对任务并发、定时作业与批处理窗口。\n"
        "- 复核近期变更、流量抖动与依赖调用耗时。",
        "### 需人工确认或审批的动作\n"
        "- 重启服务或工作进程。\n"
        "- 扩容实例、调整副本数或临时限流。\n"
        "- 修改线程池、worker 数或资源配额。",
        "### 禁止自动执行或高风险动作\n"
        "- `kill -9` 关键业务进程。\n"
        "- 未评估影响前直接改动核心调度与限流配置。\n"
        "- 未经确认执行可能导致业务中断的批量操作。",
    ]

    warning_lines = [
        "- 本轮未执行任何重启、扩容、限流或其他高风险操作。",
        "- 若后续接入危险操作工具，必须经过审批节点。",
    ]

    sections = [
        "# AIOps CPU 专项诊断报告",
        "",
    ]
    escalation_lines = _escalation_summary(state)
    if escalation_lines:
        sections.extend(["## 巡检升级说明", *escalation_lines, ""])
    sections.extend(
        [
            "## 任务与对象",
            f"- {task_text}",
            f"- 对象：`{host}`",
            "",
            "## 本地实时证据",
            *facts,
            *process_lines,
            "",
            "## 本地上下文证据",
            *context_lines,
            "",
            "## 本地 Runbook / RAG 参考",
            *runbook_lines,
            "",
        ]
    )
    if external_payload.get("content") or external_payload.get("artifacts"):
        sections.extend(
            [
                "## 外部补充参考",
                _render_external_reference(external_payload),
                "",
            ]
        )
    sections.extend(
        [
            "## 候选风险 / 待验证解释",
            *risk_lines,
            "",
            "## 证据缺口",
            *gap_lines,
            "",
            "## 处理建议",
            *recommendation_lines,
            "",
            "## 处置动作分级",
            *remediation_lines,
            "",
            "## 风险提示",
            *warning_lines,
        ]
    )
    return "\n".join(sections).strip()


def verify_cpu_investigation_report(state: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    report = str(state.get("response") or "").strip()
    findings: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []

    required_sections = [
        "## 任务与对象",
        "## 本地实时证据",
        "## 本地 Runbook / RAG 参考",
        "## 候选风险 / 待验证解释",
        "## 证据缺口",
        "## 处置动作分级",
        "## 风险提示",
    ]
    for section in required_sections:
        if section not in report:
            findings.append(f"报告缺少章节：{section}")

    summary_payload = _get_slot_payload(state, "cpu_summary")
    process_payload = _get_slot_payload(state, "top_cpu_processes")

    if _cpu_summary_is_usable(summary_payload):
        usage_percent = summary_payload.get("usage_percent")
        if usage_percent is not None and f"{usage_percent}%" not in report:
            findings.append("报告没有引用 CPU 使用率实时证据。")
    else:
        missing.append("cpu_summary")
        if "未成功获取实时 CPU 摘要" not in report:
            findings.append("报告没有明确说明 CPU 摘要证据缺口。")

    if _top_cpu_processes_is_usable(process_payload):
        first = process_payload.get("processes")[0]
        if isinstance(first, dict):
            name = str(first.get("process_name") or "")
            if name and name not in report:
                findings.append("报告没有引用热点 CPU 进程证据。")
    else:
        missing.append("top_cpu_processes")
        if "未成功获取热点 CPU 进程列表" not in report:
            findings.append("报告没有明确说明热点 CPU 进程列表缺口。")

    if "本轮未执行任何重启、扩容、限流或其他高风险操作" not in report:
        warnings.append("报告缺少危险操作未执行声明。")

    return findings, missing, warnings
