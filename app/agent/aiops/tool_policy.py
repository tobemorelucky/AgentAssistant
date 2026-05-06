"""Tool policy loading and checks."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parents[3]
TOOL_POLICY_PATH = ROOT_DIR / "tool_policy.yaml"
VALID_LEVELS = {"read_only", "low_risk", "dangerous", "blocked"}
DEFAULT_LEVEL = "blocked"


@lru_cache(maxsize=1)
def load_tool_policy() -> dict[str, Any]:
    """Load tool policy YAML."""
    if not TOOL_POLICY_PATH.exists():
        return {"tools": {}}

    data = yaml.safe_load(TOOL_POLICY_PATH.read_text(encoding="utf-8")) or {}
    tools = dict(data.get("tools", {}) or {})
    return {"tools": tools}


def get_tool_level(tool_name: str) -> str:
    """Return tool level with blocked-by-default fallback."""
    tool_config = load_tool_policy()["tools"].get(tool_name, {})
    level = tool_config.get("level", DEFAULT_LEVEL)
    return level if level in VALID_LEVELS else DEFAULT_LEVEL


def check_tool_policy(tool_name: str) -> dict[str, str]:
    """Return policy decision metadata for a tool."""
    level = get_tool_level(tool_name)
    if level == "blocked":
        return {
            "level": level,
            "decision": "reject",
            "reason": f"Tool '{tool_name}' is blocked by tool_policy.",
        }
    if level == "dangerous":
        return {
            "level": level,
            "decision": "approval_required",
            "reason": f"Tool '{tool_name}' requires human approval before execution.",
        }
    return {
        "level": level,
        "decision": "allow",
        "reason": f"Tool '{tool_name}' is allowed for automatic execution.",
    }
