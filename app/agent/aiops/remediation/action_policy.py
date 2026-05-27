"""Remediation policy checks."""

from __future__ import annotations

from app.agent.aiops.remediation.action_registry import get_action_definition


FORBIDDEN_ACTION_IDS = {
    "reboot_server",
    "delete_database_directory",
    "docker_system_prune_volumes",
}


def evaluate_action_policy(action_id: str, *, approval_token: str = "") -> dict[str, str | bool]:
    action = get_action_definition(action_id)
    if action is None:
        return {
            "allowed": False,
            "decision": "reject",
            "reason": f"Unknown remediation action: {action_id}",
            "risk_level": "forbidden",
        }

    if action.action_id in FORBIDDEN_ACTION_IDS or action.risk_level == "forbidden":
        return {
            "allowed": False,
            "decision": "reject",
            "reason": f"Action '{action_id}' is forbidden and cannot be executed automatically.",
            "risk_level": "forbidden",
        }

    if action.approval_required and not approval_token:
        return {
            "allowed": False,
            "decision": "approval_required",
            "reason": f"Action '{action_id}' requires an approval token before execution.",
            "risk_level": action.risk_level,
        }

    return {
        "allowed": True,
        "decision": "allow",
        "reason": f"Action '{action_id}' passed remediation policy checks.",
        "risk_level": action.risk_level,
    }
