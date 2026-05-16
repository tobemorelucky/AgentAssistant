"""Evidence-driven disk investigation helpers."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from .evidence import record_evidence_attempt
from .models import EvidenceStatus, InvestigationTask, StopDecision, StopDecisionType
from .profiles import DISK_PRESSURE_PROFILE, get_profile
from app.agent.aiops.disk_cleanup import normalize_disk_tool_result, summarize_disk_tool_result


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
}
REQUIRED_SLOT_ORDER = ["disk_usage", "large_directories", "large_files"]
CONDITIONAL_SLOT_ORDER = ["docker_disk_usage", "deleted_open_files"]
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
        make_task("disk_usage", required=True, reason="先确认根分区的真实磁盘使用率和主机信息。"),
        make_task("large_directories", required=True, reason="收集 Top 目录占用，定位主要容量来源。"),
        make_task("large_files", required=True, reason="收集 Top 大文件，补充目录级证据。"),
        make_task("disk_runbook", required=False, reason="补充本地 Runbook 作为处理建议参考。"),
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
        content = str(payload.get("content") if isinstance(payload, dict) else payload or "").strip()
        return (
            (EvidenceStatus.COLLECTED, "medium", "")
            if content
            else (EvidenceStatus.FAILED, "low", "Runbook retrieval returned empty content.")
        )

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

    for slot in REQUIRED_SLOT_ORDER:
        status = _slot_status(evidence_store, slot)
        attempts = _slot_attempts(evidence_store, slot)
        if not _slot_available(status) and attempts < max_attempts:
            reason = {
                "disk_usage": "必需证据：需要确认当前分区的真实磁盘使用率。",
                "large_directories": "必需证据：需要 Top 目录占用来定位主要容量来源。",
                "large_files": "必需证据：需要 Top 大文件补充解释目录占用。",
            }[slot]
            tasks.append(make_task(slot, required=True, reason=reason))

    if tasks:
        return tasks

    if _slot_attempts(evidence_store, "disk_runbook") == 0:
        tasks.append(make_task("disk_runbook", required=False, reason="补充本地 Runbook 作为处置参考。"))

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
                reason="目录证据显示存在 Docker 占用，补充 Docker 层磁盘占用。",
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
                reason="磁盘压力较高但现有目录/文件证据不足，补查 deleted open files。",
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
    if not missing_required:
        return StopDecision(
            decision=StopDecisionType.FINALIZE,
            reason="All required disk evidence slots are available.",
            missing_slots=[],
        )

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
        return "本次结论基于远程 Host Agent 实时采集数据，Runbook 仅作为参考。"
    return "本次结论基于 mock 现场数据，Runbook 仅作为参考。"


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
            lines.append(f"- 工具失败：{record.get('error_message') or '未返回目录占用结果'}")
        else:
            lines.append("- 当前未返回目录占用结果。")
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
        lines.append(f"- 权限拒绝条目：{payload.get('permission_denied_count')}")
    if not lines:
        record = _slot_record(evidence_store, "large_files")
        if record.get("status") == EvidenceStatus.FAILED.value:
            lines.append(f"- 工具失败：{record.get('error_message') or '未返回大文件结果'}")
        else:
            lines.append("- 当前未返回大文件结果。")
    return lines


def _docker_lines(evidence_store: dict[str, dict[str, Any]]) -> list[str]:
    record = _slot_record(evidence_store, "docker_disk_usage")
    payload = _slot_payload(evidence_store, "docker_disk_usage")
    if record.get("status") == EvidenceStatus.MISSING.value:
        return ["- 当前未接入 Docker 额外证据。"]
    if record.get("status") == EvidenceStatus.FAILED.value:
        return [f"- Docker 证据未成功获取：{record.get('error_message') or '工具返回失败'}"]
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
        return ["- 当前未接入 deleted open files 额外证据。"]
    if record.get("status") == EvidenceStatus.FAILED.value:
        return [f"- Deleted Open Files 证据未成功获取：{record.get('error_message') or '工具返回失败'}"]
    files = payload.get("files") or []
    lines: list[str] = []
    if files:
        for item in files[:5]:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- `{item.get('process')}` pid={item.get('pid')} 打开 `{item.get('file')}`，占用 {_number(item.get('size_gb'), 'GB')}"
            )
    else:
        lines.append("在当前过滤策略下，未发现高价值 deleted open files 证据。")
    if payload.get("filtered_out_count"):
        lines.append(f"- 已过滤掉 {payload.get('filtered_out_count')} 条 memfd/极小噪声记录。")
    return lines


def build_disk_investigation_report(state: dict[str, Any]) -> str:
    task = str(state.get("input") or "请检查服务器当前磁盘空间使用情况，并分析主要占用来源。").strip()
    evidence_store = dict(state.get("evidence_store") or {})
    source = _primary_source(evidence_store)
    disk_usage = _slot_payload(evidence_store, "disk_usage")
    large_directories = _slot_payload(evidence_store, "large_directories")
    large_files = _slot_payload(evidence_store, "large_files")
    runbook = _slot_payload(evidence_store, "disk_runbook")

    host = disk_usage.get("host") or "该字段未返回"
    mount = disk_usage.get("mount") or "/"
    facts = [
        f"- 主机：`{host}`",
        f"- 挂载点：`{mount}`",
        f"- 磁盘使用率：{_number(disk_usage.get('usage_percent'), '%')}",
        f"- 已用 / 总量 / 可用：{_number(disk_usage.get('used_gb'), 'GB')} / {_number(disk_usage.get('total_gb'), 'GB')} / {_number(disk_usage.get('available_gb'), 'GB')}",
        f"- 证据来源：{_source_statement(source)}",
    ]

    major_sources = _top_directory_lines(evidence_store) + _top_file_lines(evidence_store)
    candidate_risks = _docker_lines(evidence_store) + _deleted_lines(evidence_store)

    evidence_gaps: list[str] = []
    for slot, label in (
        ("large_directories", "Top 目录占用"),
        ("large_files", "Top 大文件"),
        ("docker_disk_usage", "Docker 磁盘占用"),
        ("deleted_open_files", "Deleted Open Files"),
    ):
        record = _slot_record(evidence_store, slot)
        if record.get("status") == EvidenceStatus.MISSING.value:
            evidence_gaps.append(f"- {label}：当前未采集。")
        elif record.get("status") == EvidenceStatus.FAILED.value:
            evidence_gaps.append(f"- {label}：{record.get('error_message') or '工具调用失败'}")
        elif record.get("status") == EvidenceStatus.PARTIAL.value:
            evidence_gaps.append(f"- {label}：证据部分可用，仍存在权限或覆盖范围边界。")

    recommendations = [
        "- 优先处理目录和大文件中最显著的容量来源，再决定是否做人工清理。",
        "- 如涉及 Docker 镜像、卷或 build cache，请先核对业务依赖后再人工执行清理。",
        "- 如存在 deleted open files，请优先评估对应进程是否需要重启或平滑发布。",
    ]
    risk_warnings = [
        "- 本次诊断没有执行任何删除、覆盖、pull、prune 或 rm -rf 操作。",
        "- 所有清理动作都应在人工确认影响范围后再执行。",
    ]
    runbook_lines = [
        f"- 查询：`{DISK_RUNBOOK_QUERY}`",
    ]
    if isinstance(runbook, dict) and runbook.get("content"):
        runbook_lines.append(f"- 摘要：{str(runbook.get('content')).strip()[:240]}")
    else:
        runbook_lines.append("- 本地 Runbook 未返回额外内容。")

    report = dedent(
        f"""
        # AIOps 磁盘诊断报告

        ## 任务与对象
        - {task}

        ## 已确认事实
        {chr(10).join(facts)}

        ## 主要容量来源
        {chr(10).join(major_sources)}

        ## 候选风险 / 待验证解释
        {chr(10).join(candidate_risks)}

        ## 证据缺口
        {chr(10).join(evidence_gaps or ["- 当前没有额外证据缺口。"])}

        ## 处理建议
        {chr(10).join(recommendations)}

        ## 风险提示
        {chr(10).join(risk_warnings)}

        ## Runbook 参考
        {chr(10).join(runbook_lines)}
        """
    ).strip()
    return report


def verify_disk_investigation_report(state: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    report = str(state.get("response") or "")
    evidence_store = dict(state.get("evidence_store") or {})
    findings: list[str] = []
    missing_evidence: list[str] = []
    risk_warnings: list[str] = []

    if "## 已确认事实" not in report or "## 证据缺口" not in report or "## 风险提示" not in report:
        findings.append("报告缺少新的磁盘 Profile 规定章节。")

    disk_usage = _slot_payload(evidence_store, "disk_usage")
    usage_percent = disk_usage.get("usage_percent")
    if usage_percent is not None and f"{usage_percent}%" not in report and f"{float(usage_percent):.1f}%" not in report:
        findings.append("报告未引用 disk_usage 里的真实磁盘使用率。")
        missing_evidence.append("disk_usage")

    for slot, label, extractor in (
        ("large_directories", "large_directories", lambda p: [item.get("path") for item in (p.get("directories") or [])[:2] if isinstance(item, dict)]),
        ("large_files", "large_files", lambda p: [item.get("path") for item in (p.get("files") or [])[:2] if isinstance(item, dict)]),
    ):
        payload = _slot_payload(evidence_store, slot)
        paths = [path for path in extractor(payload) if path]
        if paths:
            for path in paths[:2]:
                if str(path) not in report:
                    findings.append(f"报告未引用 {label} 中已采集到的关键路径 `{path}`。")
                    missing_evidence.append(slot)
                    break

    if "mock 现场数据" in report and _primary_source(evidence_store) == "remote_host":
        findings.append("remote_host 证据被误写成 mock 现场数据。")

    if "没有执行任何删除" not in report and "未执行任何删除" not in report:
        findings.append("报告缺少危险操作未执行声明。")
        risk_warnings.append("missing_safety_disclaimer")

    for slot in REQUIRED_SLOT_ORDER + CONDITIONAL_SLOT_ORDER:
        record = _slot_record(evidence_store, slot)
        if record.get("status") in {EvidenceStatus.FAILED.value, EvidenceStatus.PARTIAL.value, EvidenceStatus.MISSING.value}:
            if "## 证据缺口" not in report:
                findings.append("报告未说明证据缺口。")
                missing_evidence.append(slot)
                break

    return findings, list(dict.fromkeys(missing_evidence)), risk_warnings


def summarize_disk_investigation_task(task: dict[str, Any]) -> str:
    tool = str(task.get("tool") or "")
    slot = str(task.get("slot") or "")
    reason = str(task.get("reason") or "")
    summary = summarize_disk_tool_result(tool, {})
    return f"{slot}:{tool} | {reason}" if reason else summary
