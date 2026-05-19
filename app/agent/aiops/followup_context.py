"""Utilities for AIOps multi-turn follow-up handling."""

from __future__ import annotations

from typing import Any


DEPENDENT_FOLLOWUP_KEYWORDS = (
    "那怎么修",
    "怎么修",
    "为什么建议",
    "为什么你建议",
    "这个安全吗",
    "按你说的做了",
    "还是没效果",
    "还是没有效果",
    "没效果",
    "继续查",
    "继续查别的方法",
    "还有其他办法吗",
    "这个办法没用",
    "清理后还是不行",
)

AMBIGUOUS_FOLLOWUP_KEYWORDS = (
    "继续",
    "再看看",
    "这个呢",
)

INDEPENDENT_KEYWORDS = (
    "cpu",
    "memory",
    "内存",
    "磁盘",
    "disk",
    "巡检",
    "系统现在",
    "当前服务器",
    "当前主机",
)


def _section(report: str, heading: str) -> str:
    if not report:
        return ""
    marker = f"## {heading}"
    start = report.find(marker)
    if start < 0:
        return ""
    start = report.find("\n", start)
    if start < 0:
        return ""
    remainder = report[start + 1 :]
    next_heading = remainder.find("\n## ")
    block = remainder if next_heading < 0 else remainder[:next_heading]
    return block.strip()


def _trim_lines(text: str, *, max_lines: int = 4, max_chars: int = 360) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines[:max_lines]).strip()
    return joined[:max_chars]


def _collect_key_evidence(evidence_store: dict[str, Any]) -> list[str]:
    items: list[str] = []
    for slot, record in (evidence_store or {}).items():
        if not isinstance(record, dict):
            continue
        payload = record.get("payload") or {}
        status = str(record.get("status") or "")
        if status not in {"collected", "partial"} or not isinstance(payload, dict):
            continue
        if payload.get("usage_percent") is not None:
            items.append(f"{slot}: usage={payload.get('usage_percent')}%")
        elif isinstance(payload.get("processes"), list) and payload["processes"]:
            first = payload["processes"][0]
            if isinstance(first, dict):
                items.append(f"{slot}: top={first.get('process_name') or first.get('path')}")
        elif isinstance(payload.get("directories"), list) and payload["directories"]:
            first = payload["directories"][0]
            if isinstance(first, dict):
                items.append(f"{slot}: top_dir={first.get('path')}")
        elif isinstance(payload.get("files"), list) and payload["files"]:
            first = payload["files"][0]
            if isinstance(first, dict):
                items.append(f"{slot}: top_file={first.get('path') or first.get('file')}")
        elif payload.get("content"):
            items.append(f"{slot}: {str(payload.get('content'))[:80]}")
    return items[:8]


def _target_object(state: dict[str, Any]) -> str:
    target_alert = state.get("target_alert") or {}
    if isinstance(target_alert, dict):
        for key in ("service_name", "host", "resource_type"):
            value = target_alert.get(key)
            if value:
                return str(value)
    evidence_store = state.get("evidence_store") or {}
    if isinstance(evidence_store, dict):
        for slot in ("cpu_summary", "memory_summary", "disk_usage"):
            record = evidence_store.get(slot) or {}
            payload = record.get("payload") if isinstance(record, dict) else {}
            if isinstance(payload, dict) and payload.get("host"):
                return str(payload["host"])
    return "unknown-target"


def build_previous_aiops_context(state: dict[str, Any]) -> dict[str, Any]:
    report = str(state.get("response") or "").strip()
    evidence_store = dict(state.get("evidence_store") or {})
    recommendations = _section(report, "处理建议") or _section(report, "后续建议")
    runbook_summary = _section(report, "本地 Runbook / RAG 参考") or _section(report, "Runbook 参考")
    safety_notes = _section(report, "风险提示")
    diagnosis_summary = (
        _section(report, "专项诊断结论")
        or _section(report, "巡检结论")
        or _trim_lines(report, max_lines=6, max_chars=500)
    )
    return {
        "previous_user_query": str(state.get("input") or "").strip(),
        "previous_profile_id": ((state.get("selected_profile") or {}) or {}).get("profile_id"),
        "previous_target_object": _target_object(state),
        "previous_target_alert": state.get("target_alert") or {},
        "previous_diagnosis_summary": _trim_lines(diagnosis_summary, max_lines=6, max_chars=500),
        "previous_key_evidence": _collect_key_evidence(evidence_store),
        "previous_recommendations": _trim_lines(recommendations, max_lines=6, max_chars=500),
        "previous_runbook_summary": _trim_lines(runbook_summary, max_lines=4, max_chars=320),
        "previous_external_search_used": bool(
            _section(report, "外部补充参考") or evidence_store.get("external_reference")
        ),
        "previous_action_safety_notes": _trim_lines(safety_notes, max_lines=4, max_chars=320),
    }


def classify_followup_relation(current_user_query: str, previous_aiops_context: dict[str, Any] | None) -> dict[str, str]:
    text = (current_user_query or "").strip()
    lowered = text.lower()
    has_previous = bool(previous_aiops_context and previous_aiops_context.get("previous_user_query"))

    if not text:
        return {
            "relation_type": "ambiguous",
            "reason": "empty_query",
            "recommended_handling": "followup_decision",
        }

    if any(keyword in lowered for keyword in INDEPENDENT_KEYWORDS):
        return {
            "relation_type": "independent",
            "reason": "query_has_explicit_new_target",
            "recommended_handling": "new_diagnosis",
        }

    if any(keyword in text for keyword in DEPENDENT_FOLLOWUP_KEYWORDS):
        if has_previous:
            return {
                "relation_type": "dependent_followup",
                "reason": "query_refers_to_previous_advice_or_previous_result",
                "recommended_handling": "followup_decision",
            }
        return {
            "relation_type": "ambiguous",
            "reason": "followup_phrase_without_previous_context",
            "recommended_handling": "followup_decision",
        }

    if text in AMBIGUOUS_FOLLOWUP_KEYWORDS:
        return {
            "relation_type": "ambiguous",
            "reason": "short_followup_without_explicit_target",
            "recommended_handling": "followup_decision",
        }

    return {
        "relation_type": "independent",
        "reason": "default_to_new_diagnosis",
        "recommended_handling": "new_diagnosis",
    }


def build_followup_context_package(current_user_query: str, previous_aiops_context: dict[str, Any]) -> str:
    previous_aiops_context = previous_aiops_context or {}
    key_evidence = previous_aiops_context.get("previous_key_evidence") or []
    evidence_block = "\n".join(f"- {item}" for item in key_evidence) if key_evidence else "- No previous key evidence."
    return (
        f"Current follow-up question:\n{current_user_query}\n\n"
        f"Previous user query:\n{previous_aiops_context.get('previous_user_query', '')}\n\n"
        f"Previous profile:\n{previous_aiops_context.get('previous_profile_id', '')}\n\n"
        f"Previous target object:\n{previous_aiops_context.get('previous_target_object', '')}\n\n"
        f"Previous target alert:\n{previous_aiops_context.get('previous_target_alert', {})}\n\n"
        f"Previous diagnosis summary:\n{previous_aiops_context.get('previous_diagnosis_summary', '')}\n\n"
        f"Previous key evidence:\n{evidence_block}\n\n"
        f"Previous recommendations:\n{previous_aiops_context.get('previous_recommendations', '')}\n\n"
        f"Previous runbook summary:\n{previous_aiops_context.get('previous_runbook_summary', '')}\n\n"
        f"Previous external search used:\n{previous_aiops_context.get('previous_external_search_used', False)}\n\n"
        f"Previous safety notes:\n{previous_aiops_context.get('previous_action_safety_notes', '')}\n"
    )
