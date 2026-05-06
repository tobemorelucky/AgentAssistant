"""Agent trace event utilities."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[3]
TRACE_DIR = ROOT_DIR / "data" / "agent_traces"


def utc_now_iso() -> str:
    """UTC timestamp for trace records."""
    return datetime.now(timezone.utc).isoformat()


def summarize_result(value: Any, max_length: int = 280) -> str:
    """Create a compact human-readable summary."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value[:max_length]
    if isinstance(value, dict):
        safe_items = {key: summarize_result(val, 60) for key, val in list(value.items())[:6]}
        return json.dumps(safe_items, ensure_ascii=False)
    if isinstance(value, list):
        preview = [summarize_result(item, 40) for item in value[:5]]
        return json.dumps(preview, ensure_ascii=False)
    return str(value)[:max_length]


def create_trace_event(
    session_id: str,
    node: str,
    status: str,
    title: str,
    tool_name: str | None = None,
    tool_args: dict[str, Any] | None = None,
    result_summary: str | None = None,
    started_at: str | None = None,
    ended_at: str | None = None,
    duration_ms: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a normalized trace event."""
    started = started_at or utc_now_iso()
    ended = ended_at or started
    return {
        "trace_id": str(uuid.uuid4()),
        "session_id": session_id,
        "node": node,
        "status": status,
        "title": title,
        "tool_name": tool_name,
        "tool_args": tool_args or {},
        "result_summary": result_summary or "",
        "started_at": started,
        "ended_at": ended,
        "duration_ms": duration_ms or 0,
        "metadata": metadata or {},
    }


def append_trace_event(session_id: str, event: dict[str, Any]) -> None:
    """Persist one trace event as JSONL."""
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    trace_path = TRACE_DIR / f"{session_id}.jsonl"
    with trace_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(event, ensure_ascii=False) + "\n")
