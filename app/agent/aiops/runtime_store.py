"""File-backed runtime state and approval storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[3]
DATA_DIR = ROOT_DIR / "data"
RUNTIME_DIR = DATA_DIR / "runtime_sessions"
PENDING_DIR = DATA_DIR / "pending_actions"
INCIDENT_DIR = DATA_DIR / "incident_memory"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_path(session_id: str) -> Path:
    return RUNTIME_DIR / f"{session_id}.json"


def _pending_path(session_id: str) -> Path:
    return PENDING_DIR / f"{session_id}.json"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class RuntimeStore:
    """Manage session snapshots and pending approval actions."""

    def __init__(self) -> None:
        for directory in (RUNTIME_DIR, PENDING_DIR, INCIDENT_DIR):
            directory.mkdir(parents=True, exist_ok=True)

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        return _read_json(_runtime_path(session_id), None)

    def save_session(self, session_id: str, state: dict[str, Any], status: str) -> None:
        payload = {
            "session_id": session_id,
            "status": status,
            "updated_at": _now_iso(),
            "state": state,
        }
        _write_json(_runtime_path(session_id), payload)

    def clear_session(self, session_id: str) -> None:
        runtime_path = _runtime_path(session_id)
        if runtime_path.exists():
            runtime_path.unlink()

    def save_pending_action(self, session_id: str, action: dict[str, Any]) -> None:
        payload = {
            "session_id": session_id,
            "status": action.get("status", "pending"),
            "updated_at": _now_iso(),
            "actions": [action],
        }
        _write_json(_pending_path(session_id), payload)

    def load_pending_actions(self, session_id: str) -> dict[str, Any]:
        return _read_json(
            _pending_path(session_id),
            {"session_id": session_id, "status": "idle", "actions": []},
        )

    def update_pending_action(
        self,
        session_id: str,
        action_id: str,
        status: str,
        operator: str,
        comment: str,
    ) -> dict[str, Any]:
        payload = self.load_pending_actions(session_id)
        for action in payload.get("actions", []):
            if action.get("action_id") == action_id:
                action["status"] = status
                action["operator"] = operator
                action["comment"] = comment
                action["updated_at"] = _now_iso()
        payload["status"] = status
        payload["updated_at"] = _now_iso()
        _write_json(_pending_path(session_id), payload)
        return payload

    def clear_pending_actions(self, session_id: str) -> None:
        path = _pending_path(session_id)
        if path.exists():
            path.unlink()


runtime_store = RuntimeStore()
