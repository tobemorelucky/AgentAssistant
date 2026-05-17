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


def build_initial_memory_tasks() -> list[dict[str, Any]]:
    return [
        {
            "slot": "memory_summary",
            "tool": "get_memory_summary",
            "args": {},
            "required": True,
            "reason": "确认当前主机的内存总量、已用量、可用量和使用率。",
            "evidence_type": "memory_summary",
        },
        {
            "slot": "top_memory_processes",
            "tool": "list_top_memory_processes",
            "args": {"limit": 10},
            "required": True,
            "reason": "定位当前最主要的内存消耗进程。",
            "evidence_type": "top_memory_processes",
        },
        {
            "slot": "memory_runbook",
            "tool": "retrieve_knowledge",
            "args": {"query": MEMORY_RUNBOOK_QUERY},
            "required": False,
            "reason": "补充内存排查 Runbook 作为参考建议。",
            "evidence_type": "memory_runbook",
        },
    ]


def normalize_memory_tool_result(tool_name: str, raw_result: Any) -> dict[str, Any]:
    if tool_name == "get_memory_summary":
        payload = dict(raw_result) if isinstance(raw_result, dict) else {}
        if payload.get("ok") is False:
            return {
                "ok": False,
                "source": payload.get("source") or "unknown",
                "message": str(payload.get("message") or "内存摘要获取失败"),
                "error_code": str(payload.get("error_code") or "memory_summary_error"),
            }
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
        return result

    if tool_name == "list_top_memory_processes":
        payload = dict(raw_result) if isinstance(raw_result, dict) else {}
        if payload.get("ok") is False:
            return {
                "ok": False,
                "source": payload.get("source") or "unknown",
                "message": str(payload.get("message") or "内存热点进程获取失败"),
                "error_code": str(payload.get("error_code") or "top_memory_processes_error"),
            }
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
            "limit": int(payload.get("limit") or len(processes)),
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
        if normalized_result.get("usage_percent") is not None:
            return EvidenceStatus.COLLECTED, "high", ""
        return EvidenceStatus.PARTIAL, "low", "内存摘要未返回具体使用率"

    if slot == "top_memory_processes":
        processes = normalized_result.get("processes")
        if isinstance(processes, list) and processes:
            return EvidenceStatus.COLLECTED, "high", ""
        return EvidenceStatus.PARTIAL, "low", "未返回热点内存进程"

    if slot == "memory_runbook":
        if normalized_result.get("content"):
            return EvidenceStatus.COLLECTED, "medium", ""
        return EvidenceStatus.FAILED, "low", "未命中内存排查 Runbook"

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


def _get_slot_payload(state: dict[str, Any], slot: str) -> dict[str, Any]:
    evidence_store = dict(state.get("evidence_store") or {})
    payload = evidence_store.get(slot) or {}
    return payload if isinstance(payload, dict) else {}


def _required_missing_slots(state: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for slot in REQUIRED_SLOT_ORDER:
        payload = _get_slot_payload(state, slot)
        status = str(payload.get("status") or EvidenceStatus.MISSING)
        if status not in {EvidenceStatus.COLLECTED, EvidenceStatus.PARTIAL}:
            missing.append(slot)
    return missing


def build_follow_up_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    follow_ups: list[dict[str, Any]] = []
    max_attempts = 2
    for slot in REQUIRED_SLOT_ORDER:
        payload = _get_slot_payload(state, slot)
        status = str(payload.get("status") or EvidenceStatus.MISSING)
        attempts = int(payload.get("attempts") or 0)
        if status in {EvidenceStatus.COLLECTED, EvidenceStatus.PARTIAL}:
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
                "reason": f"补查缺失证据槽 {slot}。",
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
    payload = evidence_store.get(last_slot) or {}
    if not isinstance(payload, dict):
        return previous_no_progress_rounds + 1
    status = str(payload.get("status") or EvidenceStatus.MISSING)
    if status in {EvidenceStatus.FAILED, EvidenceStatus.MISSING}:
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
        payload = evidence_store.get(slot) or {}
        attempts = int(payload.get("attempts") or 0)
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
        return " | ".join(top_parts) if top_parts else "未返回热点内存进程"
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
    evidence_store = dict(state.get("evidence_store") or {})
    task_text = str(state.get("input") or "请检查系统当前内存情况。")

    summary_payload = _get_slot_payload(state, "memory_summary").get("payload") or {}
    process_payload = _get_slot_payload(state, "top_memory_processes").get("payload") or {}
    runbook_payload = _get_slot_payload(state, "memory_runbook").get("payload") or {}

    host = summary_payload.get("host") or "unknown-host"
    usage_percent = summary_payload.get("usage_percent")
    used_gb = summary_payload.get("used_gb")
    total_gb = summary_payload.get("total_gb")
    available_gb = summary_payload.get("available_gb")
    status = summary_payload.get("status") or "unknown"

    facts: list[str] = []
    if usage_percent is not None:
        facts.append(f"- 主机 `{host}` 当前内存使用率为 `{usage_percent}%`，已用 `{used_gb}GB` / 总量 `{total_gb}GB`。")
    else:
        facts.append("- 内存摘要未返回具体使用率。")
    if available_gb is not None:
        facts.append(f"- 当前可用内存约 `{available_gb}GB`。")
    facts.append(f"- 当前内存状态标记为 `{status}`。")

    processes = process_payload.get("processes") if isinstance(process_payload, dict) else []
    top_process_lines: list[str] = []
    if isinstance(processes, list) and processes:
        for item in processes[:5]:
            if not isinstance(item, dict):
                continue
            top_process_lines.append(
                f"- `{item.get('process_name')}` (pid={item.get('pid')}), "
                f"内存占比 `{item.get('memory_percent')}%`，RSS 约 `{item.get('rss_gb')}GB`。"
            )
    else:
        top_process_lines.append("- 当前未拿到热点内存进程列表。")

    risk_lines: list[str] = []
    if usage_percent is not None and usage_percent >= 85:
        risk_lines.append("- 当前内存水位偏高，可能导致频繁回收、申请失败或触发 OOM。")
    elif usage_percent is not None:
        risk_lines.append("- 当前内存水位未达到极高压力，但仍需关注热点进程是否持续增长。")
    else:
        risk_lines.append("- 由于缺少完整内存摘要，当前只能给出有限风险判断。")

    gap_lines: list[str] = []
    for slot in _required_missing_slots(state):
        if slot == "memory_summary":
            gap_lines.append("- 未成功获取实时内存摘要。")
        elif slot == "top_memory_processes":
            gap_lines.append("- 未成功获取热点内存进程列表。")
    if not runbook_payload.get("content"):
        gap_lines.append("- 未命中本地内存排查 Runbook。")

    suggestion_lines = [
        "- 先确认热点进程是否为预期业务进程，并检查其工作负载是否异常放大。",
        "- 若内存持续高位运行，建议结合应用侧缓存、批处理、并发参数进行容量或限流调整。",
        "- 涉及重启、缩容、清理缓存等风险操作前，应先完成业务影响评估与人工确认。",
    ]

    warning_lines = [
        "- 本报告未执行任何重启、杀进程、清缓存或其他高风险操作。",
        "- Runbook 仅作为参考，不应视为实时现场证据。",
    ]

    runbook_lines = []
    runbook_content = str(runbook_payload.get("content") or "").strip()
    if runbook_content:
        runbook_lines.append(f"- 参考摘要：{runbook_content[:240]}")
    else:
        runbook_lines.append("- 当前未命中可用的本地内存 Runbook。")

    if not gap_lines:
        gap_lines.append("- 当前关键证据已覆盖第一版 Memory Profile 所需范围。")

    return dedent(
        f"""
        # AIOps 内存诊断报告

        ## 任务与对象
        - 任务：{task_text}
        - 对象：主机 `{host}`

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
            findings.append(f"报告缺少章节：{section}")

    summary_payload = _get_slot_payload(state, "memory_summary").get("payload") or {}
    process_payload = _get_slot_payload(state, "top_memory_processes").get("payload") or {}

    if summary_payload.get("usage_percent") is not None and f"{summary_payload.get('usage_percent')}%" not in report:
        findings.append("报告未引用实际内存使用率。")
    if process_payload.get("processes"):
        first = process_payload["processes"][0]
        if isinstance(first, dict):
            name = str(first.get("process_name") or "")
            if name and name not in report:
                findings.append("报告未体现热点内存进程。")

    if "Runbook 仅作为参考" not in report:
        warnings.append("建议明确说明 Runbook 仅为参考。")

    for slot in _required_missing_slots(state):
        missing.append(slot)
        if slot == "memory_summary" and "未成功获取实时内存摘要" not in report:
            findings.append("报告未说明内存摘要缺失。")
        if slot == "top_memory_processes" and "未成功获取热点内存进程列表" not in report:
            findings.append("报告未说明热点内存进程缺失。")

    return findings, missing, warnings
