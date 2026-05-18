"""Evidence-driven runtime helpers for memory diagnosis."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from .evidence import record_evidence_attempt
from .models import EvidenceStatus, StopDecision, StopDecisionType


MEMORY_PRESSURE_PROFILE_ID = "memory_pressure_profile"
MEMORY_RUNBOOK_QUERY = "内存使用率过高 排查 runbook"
REQUIRED_SLOT_ORDER = ["memory_summary", "top_memory_processes"]
REFERENCE_SLOT_ORDER = ["memory_runbook"]
SLOT_TOOL_MAP = {
    "memory_summary": "get_memory_summary",
    "top_memory_processes": "list_top_memory_processes",
    "memory_runbook": "retrieve_knowledge",
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


def _memory_summary_is_usable(normalized_result: dict[str, Any]) -> bool:
    if normalized_result.get("ok") is False:
        return False
    if normalized_result.get("usage_percent") is not None:
        return True
    host = str(normalized_result.get("host") or "")
    has_capacity = any(
        normalized_result.get(key) is not None
        for key in ("used_gb", "total_gb", "available_gb", "swap_total_gb", "swap_used_gb")
    )
    return host not in {"", "unknown-host"} and has_capacity


def _top_memory_processes_is_usable(normalized_result: dict[str, Any]) -> bool:
    if normalized_result.get("ok") is False:
        return False
    processes = normalized_result.get("processes")
    return isinstance(processes, list) and len(processes) > 0


def _slot_is_usable(slot: str, normalized_result: dict[str, Any]) -> bool:
    if slot == "memory_summary":
        return _memory_summary_is_usable(normalized_result)
    if slot == "top_memory_processes":
        return _top_memory_processes_is_usable(normalized_result)
    if slot == "memory_runbook":
        return bool(str(normalized_result.get("content") or "").strip())
    return normalized_result.get("ok") is not False


def build_initial_memory_tasks() -> list[dict[str, Any]]:
    return [
        {
            "slot": "memory_summary",
            "tool": "get_memory_summary",
            "args": {},
            "required": True,
            "reason": "获取当前主机内存实时摘要，确认整体内存水位和剩余容量。",
            "evidence_type": "memory_summary",
        },
        {
            "slot": "top_memory_processes",
            "tool": "list_top_memory_processes",
            "args": {"limit": 10},
            "required": True,
            "reason": "获取热点内存进程列表，识别当前主要内存压力来源。",
            "evidence_type": "top_memory_processes",
        },
        {
            "slot": "memory_runbook",
            "tool": "retrieve_knowledge",
            "args": {"query": MEMORY_RUNBOOK_QUERY},
            "required": False,
            "reason": "检索内存排查 Runbook，补充处置建议和风险提示。",
            "evidence_type": "memory_runbook",
        },
    ]


def normalize_memory_tool_result(tool_name: str, raw_result: Any) -> dict[str, Any]:
    payload = dict(raw_result) if isinstance(raw_result, dict) else {}

    if tool_name == "get_memory_summary":
        if _is_error_payload(payload):
            return _failure_result(
                str(payload.get("message") or payload.get("error") or "未成功获取实时内存摘要"),
                error_code=str(payload.get("error_code") or "tool_execution_error"),
                source=str(payload.get("source") or "unknown"),
            )
        usage_percent = _to_float(payload.get("usage_percent"))
        total_gb = _to_float(payload.get("total_gb"))
        used_gb = _to_float(payload.get("used_gb"))
        available_gb = _to_float(payload.get("available_gb"))
        if available_gb is None:
            available_gb = _to_float(payload.get("free_gb"))
        result = {
            "ok": True,
            "host": payload.get("host") or payload.get("hostname") or "unknown-host",
            "usage_percent": usage_percent,
            "total_gb": total_gb,
            "used_gb": used_gb,
            "available_gb": available_gb,
            "swap_total_gb": _to_float(payload.get("swap_total_gb")),
            "swap_used_gb": _to_float(payload.get("swap_used_gb")),
            "status": payload.get("status") or _status_from_usage(usage_percent),
            "source": payload.get("source") or "unknown",
        }
        if not _memory_summary_is_usable(result):
            return _failure_result(
                "未成功获取有效内存摘要字段",
                error_code="invalid_memory_summary",
                source=str(result.get("source") or "unknown"),
            )
        return result

    if tool_name == "list_top_memory_processes":
        if _is_error_payload(payload):
            return _failure_result(
                str(payload.get("message") or payload.get("error") or "未成功获取热点内存进程列表"),
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
            rss_mb = _to_float(item.get("rss_mb"))
            rss_gb = _to_float(item.get("rss_gb"))
            if rss_gb is None and rss_mb is not None:
                rss_gb = round(rss_mb / 1024, 2)
            if rss_mb is None and rss_gb is not None:
                rss_mb = round(rss_gb * 1024, 1)
            processes.append(
                {
                    "pid": item.get("pid"),
                    "process_name": item.get("process_name") or item.get("name") or item.get("command") or "unknown",
                    "command": item.get("command") or "",
                    "memory_percent": _to_float(item.get("memory_percent")),
                    "rss_mb": rss_mb,
                    "rss_gb": rss_gb,
                    "source": item.get("source") or payload.get("source") or "unknown",
                }
            )

        return {
            "ok": True,
            "processes": processes,
            "limit": int(payload.get("limit") or len(processes) or 0),
            "message": "" if processes else "未成功获取热点内存进程列表",
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

    return {"ok": True, "source": "unknown", "payload": raw_result}


def _result_quality(slot: str, normalized_result: dict[str, Any]) -> tuple[EvidenceStatus, str, str]:
    if normalized_result.get("ok") is False:
        return EvidenceStatus.FAILED, "low", str(normalized_result.get("message") or "工具执行失败")

    if slot == "memory_summary":
        if _memory_summary_is_usable(normalized_result):
            if normalized_result.get("usage_percent") is not None:
                return EvidenceStatus.COLLECTED, "high", ""
            return EvidenceStatus.PARTIAL, "medium", "内存摘要缺少使用率，但返回了容量辅助字段"
        return EvidenceStatus.FAILED, "low", str(normalized_result.get("message") or "未成功获取实时内存摘要")

    if slot == "top_memory_processes":
        if _top_memory_processes_is_usable(normalized_result):
            return EvidenceStatus.COLLECTED, "high", ""
        return EvidenceStatus.FAILED, "low", str(normalized_result.get("message") or "未成功获取热点内存进程列表")

    if slot == "memory_runbook":
        if normalized_result.get("content"):
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "未命中内存 Runbook"

    return EvidenceStatus.PARTIAL, "unknown", ""


def update_memory_evidence_store(
    evidence_store: dict[str, dict[str, Any]],
    *,
    slot: str,
    tool_name: str,
    raw_result: Any,
) -> dict[str, dict[str, Any]]:
    normalized = normalize_memory_tool_result(tool_name, raw_result)
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
        tool_name = SLOT_TOOL_MAP[slot]
        args = {"limit": 10} if slot == "top_memory_processes" else {}
        follow_ups.append(
            {
                "slot": slot,
                "tool": tool_name,
                "args": args,
                "required": True,
                "reason": f"补查必需证据槽 {slot}，确认内存运行状态。",
                "evidence_type": slot,
            }
        )
    return follow_ups


def compute_memory_no_progress_rounds(
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


def decide_memory_stop(state: dict[str, Any]) -> StopDecision:
    evidence_store = dict(state.get("evidence_store") or {})
    investigation_round = int(state.get("investigation_round") or 0)
    no_progress_rounds = int(state.get("no_progress_rounds") or 0)
    max_rounds = 3
    max_no_progress_rounds = 1
    max_attempts = 2

    missing_slots = _required_missing_slots(state)
    if not missing_slots:
        return StopDecision(
            decision=StopDecisionType.FINALIZE,
            reason="required memory evidence collected",
            missing_slots=[],
        )

    exhausted_slots: list[str] = []
    for slot in missing_slots:
        record = evidence_store.get(slot) or {}
        attempts = int(record.get("attempts") or 0)
        if attempts >= max_attempts:
            exhausted_slots.append(slot)

    if exhausted_slots:
        return StopDecision(
            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
            reason="required memory evidence exhausted retries",
            missing_slots=missing_slots,
        )

    if investigation_round >= max_rounds:
        return StopDecision(
            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
            reason="memory investigation reached max rounds",
            missing_slots=missing_slots,
        )

    if no_progress_rounds >= max_no_progress_rounds:
        return StopDecision(
            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
            reason="memory investigation made no progress",
            missing_slots=missing_slots,
        )

    return StopDecision(
        decision=StopDecisionType.CONTINUE,
        reason="missing required memory evidence",
        missing_slots=missing_slots,
    )


def summarize_memory_tool_result(tool_name: str, normalized_result: dict[str, Any]) -> str:
    if normalized_result.get("ok") is False:
        return str(normalized_result.get("message") or f"{tool_name} 执行失败")

    if tool_name == "get_memory_summary":
        return (
            f"host={normalized_result.get('host')}, "
            f"usage={normalized_result.get('usage_percent')}%, "
            f"used={normalized_result.get('used_gb')}GB / total={normalized_result.get('total_gb')}GB"
        )
    if tool_name == "list_top_memory_processes":
        processes = normalized_result.get("processes") or []
        top_parts = []
        for item in processes[:3]:
            if not isinstance(item, dict):
                continue
            top_parts.append(
                f"{item.get('process_name')} pid={item.get('pid')} "
                f"mem={item.get('memory_percent')}% rss={item.get('rss_gb')}GB"
            )
        return " | ".join(top_parts) if top_parts else "未成功获取热点内存进程列表"
    if tool_name == "retrieve_knowledge":
        content = str(normalized_result.get("content") or "").strip()
        return content[:160] if content else "未命中内存 Runbook"
    return str(normalized_result)


def summarize_memory_investigation_task(task: dict[str, Any]) -> str:
    slot = str(task.get("slot") or "")
    tool = str(task.get("tool") or "")
    return f"{slot} -> {tool}"


def summarize_memory_evidence_store(evidence_store: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for slot in REQUIRED_SLOT_ORDER + REFERENCE_SLOT_ORDER:
        payload = evidence_store.get(slot) or {}
        if not isinstance(payload, dict):
            continue
        parts.append(f"{slot}={payload.get('status')}#{payload.get('attempts', 0)}")
    return " | ".join(parts)


def build_memory_investigation_report(state: dict[str, Any]) -> str:
    task_text = str(state.get("input") or "请检查当前内存情况")

    summary_payload = _get_slot_payload(state, "memory_summary")
    process_payload = _get_slot_payload(state, "top_memory_processes")
    runbook_payload = _get_slot_payload(state, "memory_runbook")

    host = summary_payload.get("host") or "unknown-host"
    usage_percent = summary_payload.get("usage_percent")
    used_gb = summary_payload.get("used_gb")
    total_gb = summary_payload.get("total_gb")
    available_gb = summary_payload.get("available_gb")
    status = summary_payload.get("status") or "unknown"

    facts: list[str] = []
    if _memory_summary_is_usable(summary_payload):
        if usage_percent is not None:
            facts.append(
                f"- 主机 `{host}` 的实时内存使用率为 `{usage_percent}%`，已使用 `{used_gb}GB` / 总量 `{total_gb}GB`。"
            )
        else:
            facts.append(f"- 已获取主机 `{host}` 的内存容量摘要，但未返回明确使用率。")
        if available_gb is not None:
            facts.append(f"- 当前可用内存约为 `{available_gb}GB`。")
        facts.append(f"- 当前内存状态判定为 `{status}`。")
    else:
        facts.append("- 未成功获取实时内存摘要。")

    processes = process_payload.get("processes") if isinstance(process_payload, dict) else []
    top_process_lines: list[str] = []
    if isinstance(processes, list) and processes:
        for item in processes[:5]:
            if not isinstance(item, dict):
                continue
            top_process_lines.append(
                f"- `{item.get('process_name')}` (pid={item.get('pid')}) 内存占用 `{item.get('memory_percent')}%`，RSS 约 `{item.get('rss_gb')}GB`。"
            )
    else:
        top_process_lines.append("- 未成功获取热点内存进程列表。")

    risk_lines: list[str] = []
    if usage_percent is not None and usage_percent >= 85:
        risk_lines.append("- 内存压力较高，可能引发 OOM、缓存抖动或应用响应变慢。")
    elif usage_percent is not None:
        risk_lines.append("- 当前已拿到内存水位，但仍需结合业务负载判断是否属于异常增长。")
    else:
        risk_lines.append("- 缺少实时内存摘要，暂时无法判断是否存在主机级内存压力。")

    gap_lines: list[str] = []
    missing_slots = _required_missing_slots(state)
    if "memory_summary" in missing_slots:
        gap_lines.append("- 未成功获取实时内存摘要。")
    if "top_memory_processes" in missing_slots:
        gap_lines.append("- 未成功获取热点内存进程列表。")
    if not runbook_payload.get("content"):
        gap_lines.append("- 未命中内存排查 Runbook。")
    if not gap_lines:
        gap_lines.append("- 当前关键证据已覆盖第一版 Memory Profile 所需范围。")

    suggestion_lines = [
        "- 先确认业务高峰、批处理或缓存膨胀是否与内存抬升时间一致。",
        "- 若热点进程明确，可继续核对对象缓存、连接池或大查询返回集。",
        "- 如需进一步处置，请在变更窗口内评估限流、扩容或参数调优。",
    ]

    warning_lines = [
        "- 本次结论仅基于已采集的主机内存证据和本地 Runbook 参考。",
        "- 本轮未执行任何重启、清缓存或其他高风险操作。",
    ]

    runbook_lines = []
    runbook_content = str(runbook_payload.get("content") or "").strip()
    if runbook_content:
        runbook_lines.append(f"- 参考摘要：{runbook_content[:240]}")
    else:
        runbook_lines.append("- 当前未获取到内存 Runbook。")

    return dedent(
        f"""
        # AIOps 内存诊断报告

        ## 任务与对象
        - 任务：{task_text}
        - 对象：`{host}`

        ## 已确认事实
        {chr(10).join(facts)}

        ## 当前内存状态
        {chr(10).join(facts[:2])}

        ## 主要内存消耗来源
        {chr(10).join(top_process_lines)}

        ## 候选风险 / 待验证解释
        {chr(10).join(risk_lines)}

        ## 证据缺口
        {chr(10).join(gap_lines)}

        ## 处理建议
        {chr(10).join(suggestion_lines)}

        ## 风险提示
        {chr(10).join(warning_lines)}

        ## Runbook 参考
        {chr(10).join(runbook_lines)}
        """
    ).strip()


def verify_memory_investigation_report(state: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    report = str(state.get("response") or "").strip()
    findings: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []

    required_sections = [
        "## 已确认事实",
        "## 当前内存状态",
        "## 主要内存消耗来源",
        "## 证据缺口",
        "## 风险提示",
        "## Runbook 参考",
    ]
    for section in required_sections:
        if section not in report:
            findings.append(f"报告缺少必要章节：{section}")

    summary_payload = _get_slot_payload(state, "memory_summary")
    process_payload = _get_slot_payload(state, "top_memory_processes")

    if _memory_summary_is_usable(summary_payload):
        usage_percent = summary_payload.get("usage_percent")
        if usage_percent is not None and f"{usage_percent}%" not in report:
            findings.append("报告未引用已获取的内存使用率。")
    else:
        missing.append("memory_summary")
        if "未成功获取实时内存摘要" not in report:
            findings.append("报告未说明内存摘要缺失。")

    if _top_memory_processes_is_usable(process_payload):
        first = process_payload.get("processes")[0]
        if isinstance(first, dict):
            name = str(first.get("process_name") or "")
            if name and name not in report:
                findings.append("报告未引用已获取的热点内存进程。")
    else:
        missing.append("top_memory_processes")
        if "未成功获取热点内存进程列表" not in report:
            findings.append("报告未说明热点内存进程列表缺失。")

    if "本轮未执行任何重启、清缓存或其他高风险操作" not in report:
        warnings.append("风险提示中缺少未执行高风险操作说明。")

    return findings, missing, warnings
