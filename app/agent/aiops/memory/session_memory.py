"""File-backed session memory for AIOps multi-turn context."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import config


ROOT_DIR = Path(__file__).resolve().parents[4]
SESSION_MEMORY_DIR = ROOT_DIR / "data" / "aiops_session_memory"
DEFAULT_MEMORY_PAYLOAD = {
    "session_id": "",
    "long_term_summary": "",
    "recent_turns": [],
    "turn_count": 0,
    "updated_at": "",
}
_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]+)"),
)
_SAFE_SESSION_ID_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_memory_enabled() -> bool:
    return bool(getattr(config, "aiops_session_memory_enabled", True))


def sanitize_session_id_for_filename(session_id: str | None) -> str:
    raw = str(session_id or "").strip()
    if not raw:
        return "default"
    sanitized = _SAFE_SESSION_ID_PATTERN.sub("_", raw)
    sanitized = sanitized.replace("..", "_")
    sanitized = sanitized.strip("._-")
    return sanitized or "default"


def _session_memory_path(session_id: str) -> Path:
    safe_session_id = sanitize_session_id_for_filename(session_id)
    return SESSION_MEMORY_DIR / f"{safe_session_id}.json"


def _default_memory(session_id: str) -> dict[str, Any]:
    payload = dict(DEFAULT_MEMORY_PAYLOAD)
    payload["session_id"] = session_id
    return payload


def _truncate_text(value: Any, max_length: int | None = None) -> str:
    limit = max_length or max(200, int(config.aiops_session_memory_max_turn_chars or 4000))
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(
            lambda match: f"{match.group(1)}=[REDACTED]"
            if match.lastindex and match.lastindex >= 2
            else "[REDACTED]",
            text,
        )
    return text


def _sanitize_scalar(value: Any, max_length: int | None = None) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    return _truncate_text(_redact_sensitive_text(value), max_length=max_length)


def _compact_list(items: list[Any], limit: int) -> list[Any]:
    return items[: max(0, limit)]


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        broken_path = path.with_suffix(path.suffix + ".broken")
        try:
            if broken_path.exists():
                broken_path.unlink()
            path.replace(broken_path)
        except Exception:
            pass
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def load_session_memory(session_id: str) -> dict[str, Any]:
    if not _session_memory_enabled():
        return _default_memory(session_id)

    payload = _read_json(_session_memory_path(session_id), {})
    if not isinstance(payload, dict):
        payload = {}
    memory = _default_memory(session_id)
    memory.update(payload)
    memory["session_id"] = session_id
    memory["long_term_summary"] = _sanitize_scalar(memory.get("long_term_summary"), max_length=12000) or ""
    memory["recent_turns"] = [turn for turn in list(memory.get("recent_turns") or []) if isinstance(turn, dict)]
    memory["turn_count"] = int(memory.get("turn_count") or len(memory["recent_turns"]))
    memory["updated_at"] = str(memory.get("updated_at") or "")
    return memory


def save_session_memory(session_id: str, payload: dict[str, Any]) -> None:
    if not _session_memory_enabled():
        return

    memory = _default_memory(session_id)
    memory.update(payload or {})
    memory["session_id"] = session_id
    memory["updated_at"] = _now_iso()
    memory["recent_turns"] = [turn for turn in list(memory.get("recent_turns") or []) if isinstance(turn, dict)]
    memory["turn_count"] = int(memory.get("turn_count") or len(memory["recent_turns"]))
    _write_json(_session_memory_path(session_id), memory)


def build_session_context(session_id: str) -> dict[str, Any]:
    if not _session_memory_enabled():
        return {
            "session_id": session_id,
            "long_term_summary": "",
            "recent_turns": [],
            "turn_count": 0,
            "updated_at": "",
        }

    memory = load_session_memory(session_id)
    window = max(1, int(config.aiops_session_memory_window or 20))
    return {
        "session_id": session_id,
        "long_term_summary": str(memory.get("long_term_summary") or ""),
        "recent_turns": list(memory.get("recent_turns") or [])[-window:],
        "turn_count": int(memory.get("turn_count") or 0),
        "updated_at": str(memory.get("updated_at") or ""),
    }


def _format_tool_names(tools_used: list[Any]) -> str:
    names = [str(item) for item in tools_used if item]
    if not names:
        return "none"
    return ", ".join(names[:6])


def _format_risk_events(risk_events: list[Any]) -> str:
    values = [str(item) for item in risk_events if item]
    if not values:
        return "none"
    return " | ".join(values[:3])


def _recent_turn_summary_line(turn: dict[str, Any]) -> str:
    return (
        f"User input: {_truncate_text(turn.get('user_input', ''), 300)}\n"
        f"Profile or mode: {turn.get('selected_profile') or turn.get('mode') or 'unknown'}\n"
        f"Tools used: {_format_tool_names(turn.get('tools_used') or [])}\n"
        f"Report summary: {_truncate_text(turn.get('final_report_summary') or 'none', 300)}\n"
        f"Risk events: {_format_risk_events(turn.get('risk_events') or [])}"
    )


def _fallback_summary(existing_summary: str, old_turns: list[dict[str, Any]]) -> str:
    focus = sorted(
        {
            str(turn.get("selected_profile") or turn.get("mode") or "")
            for turn in old_turns
            if turn.get("selected_profile") or turn.get("mode")
        }
    )
    tools = sorted({str(tool) for turn in old_turns for tool in list(turn.get("tools_used") or []) if tool})
    objects = sorted(
        {
            str(turn.get("evidence_summary", {}).get("target_object") or "")
            for turn in old_turns
            if isinstance(turn.get("evidence_summary"), dict)
            and turn.get("evidence_summary", {}).get("target_object")
        }
    )
    conclusions = [
        str(turn.get("final_report_summary") or "").strip()
        for turn in old_turns[-3:]
        if str(turn.get("final_report_summary") or "").strip()
    ]
    parts = []
    if existing_summary:
        parts.append(f"Previous summary: {_truncate_text(existing_summary, 1200)}")
    if objects:
        parts.append(f"Long-term target objects: {', '.join(objects[:6])}")
    if focus:
        parts.append(f"Frequent diagnosis profiles: {', '.join(focus[:6])}")
    if tools:
        parts.append(f"Frequent tools: {', '.join(tools[:10])}")
    if conclusions:
        parts.append(f"Recent conclusions: {' | '.join(conclusions[:3])}")
    parts.append("Historical conclusions are reference context only and do not replace current realtime evidence.")
    return _truncate_text("\n".join(parts), 4000)


def _truncate_prompt(prompt: str, limit: int = 12000) -> str:
    if len(prompt) <= limit:
        return prompt
    return prompt[: max(0, limit - 32)] + "\n...[prompt truncated]"


async def _summarize_turn_batch_with_llm(existing_summary: str, old_turns: list[dict[str, Any]]) -> str:
    from app.core.llm_factory import llm_factory

    llm = llm_factory.create_qwen_chat_model(
        preferred_model=config.rag_model,
        temperature=0,
        streaming=False,
    )
    turns_block = "\n\n".join(
        f"Turn {index}:\n{_recent_turn_summary_line(turn)}"
        for index, turn in enumerate(old_turns, start=1)
    )
    prompt = (
        "You are the AIOps session memory summarizer. Compress the history below into a concise long-term summary.\n"
        "Requirements:\n"
        "- Preserve long-term target assets, hosts, services, and recurring incident types.\n"
        "- Preserve validated facts and important safety/risk preferences.\n"
        "- Mark historical conclusions as historical context, not current realtime evidence.\n"
        "- Remove redundant and low-signal details.\n"
        "- Never retain tokens, passwords, api_keys, or secrets.\n"
        "- Output 6-12 short lines.\n\n"
        f"Existing long-term summary:\n{existing_summary or 'none'}\n\n"
        f"History to compress:\n{turns_block}"
    )
    prompt = _truncate_prompt(prompt, 12000)
    result = await llm.ainvoke(prompt)
    content = getattr(result, "content", result)
    if isinstance(content, list):
        content = "\n".join(str(item) for item in content if item)
    return _truncate_text(_redact_sensitive_text(content), 4000)


async def maybe_summarize_session_memory(session_id: str) -> dict[str, Any]:
    if not _session_memory_enabled():
        return _default_memory(session_id)

    memory = load_session_memory(session_id)
    window = max(1, int(config.aiops_session_memory_window or 20))
    batch = max(1, int(config.aiops_session_memory_summarize_batch or 15))
    recent_turns = list(memory.get("recent_turns") or [])
    if len(recent_turns) <= window:
        return memory

    old_turns = recent_turns[:batch]
    remaining_turns = recent_turns[batch:]
    try:
        summary = await _summarize_turn_batch_with_llm(str(memory.get("long_term_summary") or ""), old_turns)
    except Exception:
        summary = _fallback_summary(str(memory.get("long_term_summary") or ""), old_turns)

    memory["long_term_summary"] = summary
    memory["recent_turns"] = remaining_turns
    memory["updated_at"] = _now_iso()
    save_session_memory(session_id, memory)
    return memory


def compact_evidence_store(evidence_store: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for slot, record in list((evidence_store or {}).items())[:12]:
        if not isinstance(record, dict):
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        summary: dict[str, Any] = {
            "status": record.get("status"),
            "attempts": record.get("attempts"),
            "source": record.get("source"),
        }
        if payload.get("host"):
            summary["target_object"] = _sanitize_scalar(payload.get("host"), 120)
        if payload.get("usage_percent") is not None:
            summary["usage_percent"] = payload.get("usage_percent")
        if isinstance(payload.get("processes"), list) and payload["processes"]:
            first = payload["processes"][0]
            if isinstance(first, dict):
                summary["top_process"] = _sanitize_scalar(first.get("process_name") or first.get("name"), 120)
        if isinstance(payload.get("directories"), list) and payload["directories"]:
            first = payload["directories"][0]
            if isinstance(first, dict):
                summary["top_directory"] = _sanitize_scalar(first.get("path"), 160)
        if isinstance(payload.get("files"), list) and payload["files"]:
            first = payload["files"][0]
            if isinstance(first, dict):
                summary["top_file"] = _sanitize_scalar(first.get("path") or first.get("file"), 160)
        if payload.get("content"):
            summary["content"] = _sanitize_scalar(payload.get("content"), 240)
        compact[slot] = summary
    return compact


def extract_risk_events(state: dict[str, Any]) -> list[str]:
    events: list[str] = []
    for event in list(state.get("trace_events") or []):
        if not isinstance(event, dict):
            continue
        title = str(event.get("title") or "")
        status = str(event.get("status") or "")
        if status in {"warning", "error"} or "approval" in title.lower() or "forbidden" in title.lower():
            events.append(_truncate_text(title or event.get("result_summary") or "", 160))
    verifier_result = state.get("verifier_result") or {}
    for warning in list(verifier_result.get("risk_warnings") or [])[:3]:
        events.append(_truncate_text(warning, 160))
    return _compact_list(events, 10)


def summarize_report_text(report_text: Any) -> str:
    text = _truncate_text(_redact_sensitive_text(report_text), 4000)
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    summary_lines: list[str] = []
    for line in lines:
        if line.startswith("#") or line.startswith("-") or line.startswith("1."):
            summary_lines.append(line.lstrip("# ").strip())
        else:
            summary_lines.append(line)
        if len(summary_lines) >= 3:
            break
    return _truncate_text(" ".join(summary_lines), 300)


def compact_remediation_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in list(candidates or [])[:8]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "action_id": _sanitize_scalar(item.get("action_id"), 120),
                "title": _sanitize_scalar(item.get("title"), 160),
                "risk_level": _sanitize_scalar(item.get("risk_level"), 60),
                "approval_required": bool(item.get("approval_required")),
                "reason": _sanitize_scalar(item.get("reason"), 220),
            }
        )
    return compact


def _sanitize_turn_payload(turn: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(turn or {})
    sanitized["user_input"] = _sanitize_scalar(sanitized.get("user_input"), 300)
    sanitized["final_report_summary"] = _sanitize_scalar(sanitized.get("final_report_summary"), 300) or ""
    sanitized["error_summary"] = _sanitize_scalar(sanitized.get("error_summary"), 300) or ""
    sanitized["risk_events"] = [
        _sanitize_scalar(item, 160) for item in list(sanitized.get("risk_events") or [])[:3] if item
    ]
    sanitized["tools_used"] = [
        _sanitize_scalar(item, 80) for item in list(sanitized.get("tools_used") or [])[:20] if item
    ]
    return sanitized


def build_turn_summary(state: dict[str, Any], *, status: str = "completed") -> dict[str, Any]:
    selected_profile = state.get("selected_profile") or {}
    plan_source = state.get("plan_source") or ""
    target_alert = state.get("target_alert") or {}
    evidence_summary = compact_evidence_store(state.get("evidence_store") or {})
    if isinstance(target_alert, dict):
        if target_alert.get("host"):
            evidence_summary.setdefault("target_object", _sanitize_scalar(target_alert.get("host"), 120))
        elif target_alert.get("service_name"):
            evidence_summary.setdefault("target_object", _sanitize_scalar(target_alert.get("service_name"), 120))
    error_summary = ""
    if status == "failed":
        error_summary = (
            _sanitize_scalar(state.get("error_summary"), 300)
            or _sanitize_scalar(state.get("response"), 300)
            or "unknown error"
        )
    return {
        "turn_id": str(uuid.uuid4()),
        "created_at": _now_iso(),
        "status": status,
        "user_input": _sanitize_scalar(state.get("input"), max_length=300),
        "mode": _sanitize_scalar(state.get("mode"), 60),
        "selected_profile": _sanitize_scalar(
            selected_profile.get("profile_id") if isinstance(selected_profile, dict) else selected_profile,
            120,
        ),
        "plan_source": _sanitize_scalar(plan_source, 120),
        "tools_used": _compact_list(
            [_sanitize_scalar(tool, 80) for tool in list(state.get("tools_used") or []) if tool],
            20,
        ),
        "evidence_summary": evidence_summary,
        "verifier_passed": (state.get("verifier_result") or {}).get("passed"),
        "risk_events": extract_risk_events(state),
        "final_report_summary": summarize_report_text(state.get("response", "")),
        "error_summary": error_summary,
        "remediation_candidates": compact_remediation_candidates(state.get("remediation_candidates") or []),
        "target_alert": {
            "alert_name": _sanitize_scalar(target_alert.get("alert_name"), 120),
            "service_name": _sanitize_scalar(target_alert.get("service_name"), 120),
            "host": _sanitize_scalar(target_alert.get("host"), 120),
        }
        if isinstance(target_alert, dict) and target_alert
        else {},
    }


async def append_session_turn(session_id: str, turn: dict[str, Any]) -> dict[str, Any]:
    if not _session_memory_enabled():
        return build_session_context(session_id)

    memory = load_session_memory(session_id)
    memory["recent_turns"] = list(memory.get("recent_turns") or []) + [_sanitize_turn_payload(turn)]
    memory["turn_count"] = int(memory.get("turn_count") or 0) + 1
    memory["updated_at"] = _now_iso()
    save_session_memory(session_id, memory)
    return await maybe_summarize_session_memory(session_id)
