"""Evidence-driven disk investigation helpers."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from app.agent.aiops.disk_cleanup import normalize_disk_tool_result, summarize_disk_tool_result
from app.agent.aiops.utils import normalize_external_reference_result

from .evidence import record_evidence_attempt
from .models import EvidenceStatus, InvestigationTask, StopDecision, StopDecisionType
from .profiles import DISK_PRESSURE_PROFILE, get_profile


DISK_PRESSURE_PROFILE_ID = DISK_PRESSURE_PROFILE.profile_id
DISK_RUNBOOK_QUERY = "磁盘使用率过高 清理 runbook"
DISK_TOOL_ARGS: dict[str, dict[str, Any]] = {
    "get_disk_usage": {"mount": "/"},
    "list_large_directories": {"path": "/", "limit": 10},
    "list_large_files": {"path": "/", "min_size_mb": 100, "limit": 20},
    "query_docker_disk_usage": {},
    "query_deleted_open_files": {},
    "retrieve_knowledge": {"query": DISK_RUNBOOK_QUERY},
}
SLOT_TOOL_MAP = {
    "disk_usage": "get_disk_usage",
    "large_directories": "list_large_directories",
    "large_files": "list_large_files",
    "docker_disk_usage": "query_docker_disk_usage",
    "deleted_open_files": "query_deleted_open_files",
    "disk_runbook": "retrieve_knowledge",
    "service_context": "get_service_info",
    "historical_tickets": "search_historical_tickets",
    "external_reference": "web_search",
}
REQUIRED_SLOT_ORDER = ["disk_usage", "large_directories", "large_files"]
CONDITIONAL_SLOT_ORDER = ["docker_disk_usage", "deleted_open_files", "service_context", "historical_tickets", "external_reference"]
REFERENCE_SLOT_ORDER = ["disk_runbook"]


def _model_to_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def is_disk_pressure_profile(selected_profile: dict[str, Any] | None) -> bool:
    return isinstance(selected_profile, dict) and selected_profile.get("profile_id") == DISK_PRESSURE_PROFILE_ID


def make_task(
    slot: str,
    *,
    required: bool,
    reason: str,
    args: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task = InvestigationTask(
        slot=slot,
        tool=SLOT_TOOL_MAP[slot],
        args=args if args is not None else dict(DISK_TOOL_ARGS.get(SLOT_TOOL_MAP[slot], {})),
        required=required,
        reason=reason,
    )
    return _model_to_dict(task)


def build_initial_disk_tasks() -> list[dict[str, Any]]:
    return [
        make_task("disk_usage", required=True, reason="Collect the real-time disk usage baseline."),
        make_task("large_directories", required=True, reason="Collect top large directories to identify capacity hotspots."),
        make_task("large_files", required=True, reason="Collect top large files to identify concrete capacity consumers."),
        make_task("disk_runbook", required=False, reason="Retrieve the local disk cleanup runbook."),
    ]


def _record_status(status: str) -> EvidenceStatus:
    return EvidenceStatus(status)


def _has_numeric(value: Any) -> bool:
    return isinstance(value, (int, float))


def _extract_slot_source(payload: dict[str, Any]) -> str:
    return str(payload.get("source") or "")


def _status_quality_error(slot: str, payload: Any) -> tuple[EvidenceStatus, str, str]:
    if not isinstance(payload, dict):
        return EvidenceStatus.FAILED, "low", "Tool returned a non-dict payload."
    if payload.get("ok") is False or payload.get("error"):
        return EvidenceStatus.FAILED, "low", str(payload.get("message") or payload.get("error") or "Tool failed.")

    if slot == "disk_usage":
        if _has_numeric(payload.get("usage_percent")):
            return EvidenceStatus.COLLECTED, "high", ""
        return EvidenceStatus.FAILED, "low", "Disk usage percentage was not returned."

    if slot == "large_directories":
        directories = payload.get("directories") or []
        if directories:
            return EvidenceStatus.COLLECTED, "high", ""
        return EvidenceStatus.PARTIAL, "medium", "No large directory entries were returned."

    if slot == "large_files":
        files = payload.get("files") or []
        if files:
            if payload.get("scan_incomplete"):
                return EvidenceStatus.PARTIAL, "medium", "Large-file scan was incomplete due to permission limits."
            return EvidenceStatus.COLLECTED, "high", ""
        if payload.get("scan_incomplete"):
            return EvidenceStatus.PARTIAL, "medium", "Large-file scan returned no entries and was incomplete."
        return EvidenceStatus.PARTIAL, "medium", "No large-file entries were returned."

    if slot == "docker_disk_usage":
        fields = (
            payload.get("images_gb"),
            payload.get("containers_gb"),
            payload.get("volumes_gb"),
            payload.get("build_cache_gb"),
            payload.get("total_gb"),
        )
        if any(value is not None for value in fields):
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "Docker disk-usage fields were not returned."

    if slot == "deleted_open_files":
        return EvidenceStatus.COLLECTED, "medium", ""

    if slot == "disk_runbook":
        content = str(payload.get("content") or "").strip()
        if content:
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "Runbook retrieval returned empty content."

    if slot == "service_context":
        if payload.get("service_name"):
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "Service context was not returned."

    if slot == "historical_tickets":
        if isinstance(payload.get("tickets"), list):
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "Historical tickets were not returned."

    if slot == "external_reference":
        if str(payload.get("content") or "").strip():
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "External reference returned no usable content."

    return EvidenceStatus.COLLECTED, "unknown", ""


def update_disk_evidence_store(
    evidence_store: dict[str, dict[str, Any]],
    *,
    slot: str,
    tool_name: str,
    raw_result: Any,
) -> dict[str, dict[str, Any]]:
    normalized = normalize_disk_tool_result(tool_name, raw_result)
    if slot == "disk_runbook" and isinstance(normalized, str):
        normalized = {"content": normalized, "source": "retrieve_knowledge"}
    if slot == "service_context" and isinstance(raw_result, dict):
        normalized = {
            "ok": raw_result.get("ok") is not False and not raw_result.get("error") and not raw_result.get("error_code"),
            "service_name": raw_result.get("service_name"),
            "owner_team": raw_result.get("owner_team"),
            "deployment": raw_result.get("deployment"),
            "dependencies": raw_result.get("dependencies") or [],
            "source": raw_result.get("source") or "unknown",
            "message": raw_result.get("message") or raw_result.get("error") or "",
            "error_code": raw_result.get("error_code") or ("tool_execution_error" if raw_result.get("error") else ""),
        }
    if slot == "historical_tickets" and isinstance(raw_result, dict):
        normalized = {
            "ok": raw_result.get("ok") is not False and not raw_result.get("error") and not raw_result.get("error_code"),
            "tickets": raw_result.get("tickets") or [],
            "total": raw_result.get("total") or len(raw_result.get("tickets") or []),
            "source": raw_result.get("source") or "unknown",
            "message": raw_result.get("message") or raw_result.get("error") or "",
            "error_code": raw_result.get("error_code") or ("tool_execution_error" if raw_result.get("error") else ""),
        }
    if slot == "external_reference":
        normalized = normalize_external_reference_result(raw_result)
    status, quality, error_message = _status_quality_error(slot, normalized)
    return record_evidence_attempt(
        evidence_store,
        slot=slot,
        status=status,
        source=_extract_slot_source(normalized) or tool_name,
        payload=normalized,
        quality=quality,
        error_message=error_message,
    )


def _slot_record(evidence_store: dict[str, dict[str, Any]], slot: str) -> dict[str, Any]:
    return dict(evidence_store.get(slot) or {"slot": slot, "status": EvidenceStatus.MISSING, "attempts": 0})


def _slot_payload(evidence_store: dict[str, dict[str, Any]], slot: str) -> dict[str, Any]:
    payload = _slot_record(evidence_store, slot).get("payload")
    return payload if isinstance(payload, dict) else {}


def _slot_attempts(evidence_store: dict[str, dict[str, Any]], slot: str) -> int:
    return int(_slot_record(evidence_store, slot).get("attempts") or 0)


def _slot_status(evidence_store: dict[str, dict[str, Any]], slot: str) -> str:
    return str(_slot_record(evidence_store, slot).get("status") or EvidenceStatus.MISSING)


def _slot_available(status: str) -> bool:
    return status in {EvidenceStatus.COLLECTED.value, EvidenceStatus.PARTIAL.value}


def _max_attempts(profile_id: str | None) -> int:
    profile = get_profile(profile_id)
    if profile is None:
        return 2
    return max(1, int(profile.stop_rules.get("max_attempts_per_slot", 2)))


def _max_rounds(profile_id: str | None) -> int:
    profile = get_profile(profile_id)
    if profile is None:
        return 4
    return max(1, int(profile.stop_rules.get("max_rounds", 4)))


def _max_no_progress_rounds(profile_id: str | None) -> int:
    profile = get_profile(profile_id)
    if profile is None:
        return 2
    return max(0, int(profile.stop_rules.get("max_no_progress_rounds", 2)))


def _directories_indicate_docker(evidence_store: dict[str, dict[str, Any]]) -> bool:
    payload = _slot_payload(evidence_store, "large_directories")
    directories = payload.get("directories") or []
    for item in directories:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").lower()
        if "docker" in path or "/var/lib/containerd" in path:
            return True
    return False


def _disk_pressure_high(evidence_store: dict[str, dict[str, Any]]) -> bool:
    usage = _slot_payload(evidence_store, "disk_usage").get("usage_percent")
    return isinstance(usage, (int, float)) and usage >= 80


def _explanation_is_weak(evidence_store: dict[str, dict[str, Any]]) -> bool:
    directory_count = len(_slot_payload(evidence_store, "large_directories").get("directories") or [])
    file_count = len(_slot_payload(evidence_store, "large_files").get("files") or [])
    return directory_count < 2 or file_count < 2


def build_follow_up_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_store = dict(state.get("evidence_store") or {})
    profile_id = (state.get("selected_profile") or {}).get("profile_id")
    max_attempts = _max_attempts(profile_id)
    tasks: list[dict[str, Any]] = []
    target_alert = state.get("target_alert") or {}
    service_name = str(target_alert.get("service_name") or "")

    for slot in REQUIRED_SLOT_ORDER:
        status = _slot_status(evidence_store, slot)
        attempts = _slot_attempts(evidence_store, slot)
        if not _slot_available(status) and attempts < max_attempts:
            reason = {
                "disk_usage": "Retry the real-time disk usage summary.",
                "large_directories": "Retry the top large-directories scan.",
                "large_files": "Retry the top large-files scan.",
            }[slot]
            tasks.append(make_task(slot, required=True, reason=reason))

    if tasks:
        return tasks

    if _slot_attempts(evidence_store, "disk_runbook") == 0:
        tasks.append(make_task("disk_runbook", required=False, reason="Retrieve the local disk runbook reference."))

    if service_name and _slot_attempts(evidence_store, "service_context") == 0:
        tasks.append(
            make_task(
                "service_context",
                required=False,
                reason="Collect local service context because the alert contains a service_name.",
                args={"service_name": service_name},
            )
        )
        tasks.append(
            make_task(
                "historical_tickets",
                required=False,
                reason="Collect local historical tickets for the alerted service.",
                args={"service_name": service_name, "alert_name": target_alert.get("alert_name"), "limit": 5},
            )
        )

    docker_status = _slot_status(evidence_store, "docker_disk_usage")
    if (
        docker_status == EvidenceStatus.MISSING.value
        and _slot_attempts(evidence_store, "docker_disk_usage") < max_attempts
        and (_directories_indicate_docker(evidence_store) or _disk_pressure_high(evidence_store))
    ):
        tasks.append(
            make_task(
                "docker_disk_usage",
                required=False,
                reason="Collect Docker disk-usage evidence when disk pressure is high or Docker paths dominate.",
            )
        )

    deleted_status = _slot_status(evidence_store, "deleted_open_files")
    if (
        deleted_status == EvidenceStatus.MISSING.value
        and _slot_attempts(evidence_store, "deleted_open_files") < max_attempts
        and (_disk_pressure_high(evidence_store) and _explanation_is_weak(evidence_store))
    ):
        tasks.append(
            make_task(
                "deleted_open_files",
                required=False,
                reason="Check deleted-open-files when disk pressure remains unexplained.",
            )
        )

    runbook_payload = _slot_payload(evidence_store, "disk_runbook")
    if (
        _slot_attempts(evidence_store, "external_reference") == 0
        and (bool(state.get("remediation_feedback_failed")) or not str(runbook_payload.get("content") or "").strip())
    ):
        query = (
            f"disk usage high {service_name} {target_alert.get('alert_name') or 'HighDiskUsage'} cleanup best practice"
            if service_name
            else "disk usage high docker build cache cleanup best practice"
        )
        tasks.append(
            make_task(
                "external_reference",
                required=False,
                reason="Local runbook is insufficient or the user reported the previous guidance was ineffective.",
                args={"query": query},
            )
        )

    deduped: list[dict[str, Any]] = []
    seen_slots: set[str] = set()
    for task in tasks:
        slot = str(task.get("slot") or "")
        if slot in seen_slots:
            continue
        seen_slots.add(slot)
        deduped.append(task)
    return deduped


def _required_missing_slots(evidence_store: dict[str, dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for slot in REQUIRED_SLOT_ORDER:
        if not _slot_available(_slot_status(evidence_store, slot)):
            missing.append(slot)
    return missing


def decide_disk_stop(state: dict[str, Any]) -> StopDecision:
    selected_profile = state.get("selected_profile") or {}
    profile_id = selected_profile.get("profile_id")
    evidence_store = dict(state.get("evidence_store") or {})
    investigation_round = int(state.get("investigation_round") or 0)
    no_progress_rounds = int(state.get("no_progress_rounds") or 0)
    missing_required = _required_missing_slots(evidence_store)
    if missing_required:
        max_attempts = _max_attempts(profile_id)
        exhausted = all(_slot_attempts(evidence_store, slot) >= max_attempts for slot in missing_required)
        if exhausted:
            return StopDecision(
                decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                reason="Required disk evidence slots reached the maximum attempts without better evidence.",
                missing_slots=missing_required,
            )
        if investigation_round >= _max_rounds(profile_id):
            return StopDecision(
                decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                reason="Disk investigation reached the maximum round limit.",
                missing_slots=missing_required,
            )
        if no_progress_rounds >= _max_no_progress_rounds(profile_id):
            return StopDecision(
                decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                reason="Disk investigation made no progress across repeated rounds.",
                missing_slots=missing_required,
            )
        return StopDecision(
            decision=StopDecisionType.CONTINUE,
            reason="More disk evidence should be collected.",
            missing_slots=missing_required,
        )

    if build_follow_up_tasks(state):
        return StopDecision(
            decision=StopDecisionType.CONTINUE,
            reason="Collecting optional disk evidence and references.",
            missing_slots=[],
        )

    return StopDecision(
        decision=StopDecisionType.FINALIZE,
        reason="All required disk evidence slots are available.",
        missing_slots=[],
    )


def compute_no_progress_rounds(
    evidence_store: dict[str, dict[str, Any]],
    *,
    previous_no_progress_rounds: int,
    last_slot: str | None,
) -> int:
    if not last_slot:
        return previous_no_progress_rounds
    record = _slot_record(evidence_store, last_slot)
    status = str(record.get("status") or EvidenceStatus.MISSING)
    if status in {EvidenceStatus.COLLECTED.value, EvidenceStatus.PARTIAL.value}:
        return 0
    return previous_no_progress_rounds + 1


def summarize_evidence_store(evidence_store: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for slot in REQUIRED_SLOT_ORDER + CONDITIONAL_SLOT_ORDER + REFERENCE_SLOT_ORDER:
        record = _slot_record(evidence_store, slot)
        parts.append(f"{slot}={record.get('status')}#{record.get('attempts', 0)}")
    return " | ".join(parts)


def _primary_source(evidence_store: dict[str, dict[str, Any]]) -> str:
    for slot in REQUIRED_SLOT_ORDER + CONDITIONAL_SLOT_ORDER:
        source = _slot_record(evidence_store, slot).get("source")
        if source in {"remote_host", "mock"}:
            return str(source)
    return "mock"


def _source_statement(source: str) -> str:
    if source == "remote_host":
        return "本轮结论基于远程 Host Agent 的实时采集数据，并辅以本地 Runbook 参考。"
    return "本轮结论基于 mock 现场数据，并辅以本地 Runbook 参考。"


def _number(value: Any, suffix: str = "") -> str:
    if isinstance(value, (int, float)):
        if float(value).is_integer():
            return f"{int(value)}{suffix}"
        return f"{value:.1f}{suffix}"
    return "该字段未返回"


def _top_directory_lines(evidence_store: dict[str, dict[str, Any]]) -> list[str]:
    payload = _slot_payload(evidence_store, "large_directories")
    directories = payload.get("directories") or []
    lines = [
        f"- `{item.get('path')}`：{_number(item.get('size_gb'), 'GB')}"
        for item in directories[:5]
        if isinstance(item, dict)
    ]
    if not lines:
        record = _slot_record(evidence_store, "large_directories")
        if record.get("status") == EvidenceStatus.FAILED.value:
            lines.append(f"- 调用失败：{record.get('error_message') or '目录扫描失败'}")
        else:
            lines.append("- 未返回目录占用结果。")
    return lines


def _top_file_lines(evidence_store: dict[str, dict[str, Any]]) -> list[str]:
    payload = _slot_payload(evidence_store, "large_files")
    files = payload.get("files") or []
    lines = [
        f"- `{item.get('path')}`：{_number(item.get('size_gb'), 'GB')}"
        for item in files[:5]
        if isinstance(item, dict)
    ]
    if payload.get("scan_incomplete"):
        lines.append("- 本次扫描存在权限跳过，结果可能不完整。")
    if payload.get("permission_denied_count"):
        lines.append(f"- permission denied 计数：{payload.get('permission_denied_count')}")
    if not lines:
        record = _slot_record(evidence_store, "large_files")
        if record.get("status") == EvidenceStatus.FAILED.value:
            lines.append(f"- 调用失败：{record.get('error_message') or '大文件扫描失败'}")
        else:
            lines.append("- 未返回大文件结果。")
    return lines


def _docker_lines(evidence_store: dict[str, dict[str, Any]]) -> list[str]:
    record = _slot_record(evidence_store, "docker_disk_usage")
    payload = _slot_payload(evidence_store, "docker_disk_usage")
    if record.get("status") == EvidenceStatus.MISSING.value:
        return ["- 当前尚未采集 Docker 磁盘占用证据。"]
    if record.get("status") == EvidenceStatus.FAILED.value:
        return [f"- Docker 证据获取失败：{record.get('error_message') or 'Docker disk usage failed'}"]
    return [
        f"- images：{_number(payload.get('images_gb'), 'GB')}",
        f"- containers：{_number(payload.get('containers_gb'), 'GB')}",
        f"- volumes：{_number(payload.get('volumes_gb'), 'GB')}",
        f"- build cache：{_number(payload.get('build_cache_gb'), 'GB')}",
        f"- total：{_number(payload.get('total_gb'), 'GB')}",
    ]


def _deleted_lines(evidence_store: dict[str, dict[str, Any]]) -> list[str]:
    record = _slot_record(evidence_store, "deleted_open_files")
    payload = _slot_payload(evidence_store, "deleted_open_files")
    if record.get("status") == EvidenceStatus.MISSING.value:
        return ["- 当前尚未采集 deleted open files 证据。"]
    if record.get("status") == EvidenceStatus.FAILED.value:
        return [f"- Deleted open files 获取失败：{record.get('error_message') or 'tool failed'}"]
    files = payload.get("files") or []
    lines: list[str] = []
    if files:
        for item in files[:5]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('process')}` pid={item.get('pid')} 打开 `{item.get('file')}`，大小 { _number(item.get('size_gb'), 'GB') }"
            )
    else:
        lines.append("- 在当前过滤策略下，未发现高价值 deleted open files 证据。")
    if payload.get("filtered_out_count"):
        lines.append(f"- 已过滤掉 {payload.get('filtered_out_count')} 条 memfd/极小噪声记录。")
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


def _escalation_summary(state: dict[str, Any]) -> list[str]:
    reason = str(state.get("escalation_reason") or "").strip()
    target_alert = state.get("target_alert") or {}
    if not reason and not target_alert:
        return []
    lines = []
    if target_alert:
        lines.append(
            f"- 本轮基础巡检发现 `{target_alert.get('alert_name', 'unknown-alert')}` / "
            f"`{target_alert.get('severity', 'unknown')}`，系统自动升级进入 Disk 专项诊断。"
        )
    if reason:
        lines.append(f"- 升级原因：{reason}")
    return lines


def build_disk_investigation_report(state: dict[str, Any]) -> str:
    task = str(state.get("input") or "请检查服务器当前磁盘空间使用情况，并分析主要占用来源。").strip()
    evidence_store = dict(state.get("evidence_store") or {})
    source = _primary_source(evidence_store)
    disk_usage = _slot_payload(evidence_store, "disk_usage")
    runbook = _slot_payload(evidence_store, "disk_runbook")
    external_reference = _slot_payload(evidence_store, "external_reference")
    service_payload = _slot_payload(evidence_store, "service_context")
    tickets_payload = _slot_payload(evidence_store, "historical_tickets")

    host = disk_usage.get("host") or "unknown-host"
    mount = disk_usage.get("mount") or "/"
    facts = [
        f"- 主机：`{host}`",
        f"- 挂载点：`{mount}`",
        f"- 磁盘使用率：{_number(disk_usage.get('usage_percent'), '%')}",
        f"- 已用 / 总量 / 可用：{_number(disk_usage.get('used_gb'), 'GB')} / {_number(disk_usage.get('total_gb'), 'GB')} / {_number(disk_usage.get('available_gb'), 'GB')}",
        f"- 证据来源说明：{_source_statement(source)}",
    ]

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

    evidence_gaps: list[str] = []
    for slot, label in (
        ("large_directories", "Top 目录占用"),
        ("large_files", "Top 大文件"),
        ("docker_disk_usage", "Docker 磁盘占用"),
        ("deleted_open_files", "Deleted Open Files"),
    ):
        record = _slot_record(evidence_store, slot)
        if record.get("status") == EvidenceStatus.MISSING.value:
            evidence_gaps.append(f"- 未采集 `{label}` 证据。")
        elif record.get("status") == EvidenceStatus.FAILED.value:
            evidence_gaps.append(f"- `{label}` 采集失败：{record.get('error_message') or 'tool failed'}")
        elif record.get("status") == EvidenceStatus.PARTIAL.value:
            evidence_gaps.append(f"- `{label}` 仅采集到部分结果，结论存在边界。")
    if not str(runbook.get("content") or "").strip():
        evidence_gaps.append("- 本地 Runbook / RAG 尚未给出有效磁盘处置方案。")
    if external_reference.get("ok") is False:
        evidence_gaps.append(f"- 外部补充参考获取失败：{external_reference.get('message') or 'web_search failed'}")
    if not evidence_gaps:
        evidence_gaps.append("- 当前关键证据已覆盖第一版 Disk Profile 所需范围。")

    runbook_lines = (
        [f"- 本地 Runbook 摘要：{str(runbook.get('content')).strip()[:260]}"]
        if isinstance(runbook, dict) and runbook.get("content")
        else ["- 本地知识库未返回有效磁盘处置方案。"]
    )

    risk_warnings = [
        "- 本轮未执行任何重启、清理、扩容、限流或其他高风险操作。",
        "- `rm -rf`、`docker system prune --volumes`、删除数据库目录、删除业务 uploads、删除 Milvus/MinIO/etcd volumes 均属于高风险或禁止自动执行动作。",
        "- 若后续接入危险操作工具，必须经过审批节点。",
    ]

    recommendations = [
        "- 继续核对目录占用、大文件与 Docker build cache 的相对贡献，确认主要容量来源。",
        "- 若存在 deleted open files，可结合进程释放策略或平滑重启窗口进一步处理。",
        "- 如服务级上下文存在，可继续补充历史工单或服务信息辅助判断影响面。",
    ]

    remediation_lines = [
        "### 可直接给出的低风险建议\n"
        "- 继续观察磁盘趋势、目录占用和 Docker cache 变化。\n"
        "- 补充确认业务峰值、日志增长和临时目录使用情况。\n"
        "- 在不改动数据的前提下复核路径归属和容量变化。",
        "### 需人工确认或审批的动作\n"
        "- 清理 Docker build cache 或无用镜像。\n"
        "- 删除已确认可清理的临时目录、归档日志或缓存目录。\n"
        "- 重启长时间持有 deleted-open-files 的业务进程。",
        "### 禁止自动执行或高风险动作\n"
        "- `rm -rf` 未确认路径。\n"
        "- 删除数据库目录、持久化卷或业务 uploads 目录。\n"
        "- 直接删除 Milvus / MinIO / etcd volume 数据。",
    ]

    sections = [
        "# AIOps 磁盘专项诊断报告",
        "",
    ]
    escalation_lines = _escalation_summary(state)
    if escalation_lines:
        sections.extend(["## 巡检升级说明", *escalation_lines, ""])
    sections.extend(
        [
            "## 任务与对象",
            f"- {task}",
            "",
            "## 已确认事实",
            *facts,
            "",
            "## 本地实时证据",
            "### Top 目录占用",
            *_top_directory_lines(evidence_store),
            "### Top 大文件",
            *_top_file_lines(evidence_store),
            "### Docker 占用",
            *_docker_lines(evidence_store),
            "### Deleted Open Files",
            *_deleted_lines(evidence_store),
            "",
            "## 本地上下文证据",
            *context_lines,
            "",
            "## 本地 Runbook / RAG 参考",
            *runbook_lines,
            "",
        ]
    )
    if external_reference.get("content") or external_reference.get("artifacts"):
        sections.extend(
            [
                "## 外部补充参考",
                _render_external_reference(external_reference),
                "",
            ]
        )
    sections.extend(
        [
            "## 候选风险 / 待验证解释",
            "- 目录和文件占用结果用于解释当前容量压力；若存在 partial scan，需要结合权限边界理解结果。",
            "- Docker 占用和 deleted open files 用于补充解释无法仅靠目录/文件结果说明的磁盘压力。",
            "",
            "## 证据缺口",
            *evidence_gaps,
            "",
            "## 处理建议",
            *recommendations,
            "",
            "## 处置动作分级",
            *remediation_lines,
            "",
            "## 风险提示",
            *risk_warnings,
        ]
    )
    return "\n".join(sections).strip()


def verify_disk_investigation_report(state: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    report = str(state.get("response") or "")
    evidence_store = dict(state.get("evidence_store") or {})
    findings: list[str] = []
    missing_evidence: list[str] = []
    risk_warnings: list[str] = []

    required_sections = [
        "## 任务与对象",
        "## 已确认事实",
        "## 本地实时证据",
        "## 本地 Runbook / RAG 参考",
        "## 证据缺口",
        "## 处置动作分级",
        "## 风险提示",
    ]
    for section in required_sections:
        if section not in report:
            findings.append(f"报告缺少章节：{section}")

    disk_usage = _slot_payload(evidence_store, "disk_usage")
    usage_percent = disk_usage.get("usage_percent")
    if usage_percent is not None and f"{usage_percent}%" not in report and f"{float(usage_percent):.1f}%" not in report:
        findings.append("报告没有引用磁盘使用率实时证据。")
        missing_evidence.append("disk_usage")

    for slot, label in (("large_directories", "large_directories"), ("large_files", "large_files")):
        payload = _slot_payload(evidence_store, slot)
        key = "directories" if slot == "large_directories" else "files"
        items = payload.get(key) or []
        if items:
            first = items[0]
            if isinstance(first, dict) and str(first.get("path") or "") not in report:
                findings.append(f"报告没有引用 {label} 实时证据。")
                missing_evidence.append(slot)

    if "本轮未执行任何重启、清理、扩容、限流或其他高风险操作" not in report:
        findings.append("报告缺少危险操作未执行声明。")
        risk_warnings.append("missing_safety_disclaimer")

    for slot in REQUIRED_SLOT_ORDER + CONDITIONAL_SLOT_ORDER:
        record = _slot_record(evidence_store, slot)
        if record.get("status") in {EvidenceStatus.FAILED.value, EvidenceStatus.PARTIAL.value, EvidenceStatus.MISSING.value}:
            if "## 证据缺口" not in report:
                findings.append("报告缺少证据缺口章节。")
                missing_evidence.append(slot)
                break

    return findings, list(dict.fromkeys(missing_evidence)), risk_warnings


def summarize_disk_investigation_task(task: dict[str, Any]) -> str:
    tool = str(task.get("tool") or "")
    slot = str(task.get("slot") or "")
    reason = str(task.get("reason") or "")
    summary = summarize_disk_tool_result(tool, {})
    return f"{slot}:{tool} | {reason}" if reason else summary
