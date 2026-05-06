from app.agent.aiops import tool_policy


def test_unknown_tool_defaults_to_blocked(monkeypatch):
    monkeypatch.setattr(tool_policy, "load_tool_policy", lambda: {"tools": {}})
    assert tool_policy.get_tool_level("unknown_tool") == "blocked"


def test_dangerous_tool_requires_approval(monkeypatch):
    monkeypatch.setattr(
        tool_policy,
        "load_tool_policy",
        lambda: {"tools": {"run_remote_command": {"level": "dangerous"}}},
    )

    decision = tool_policy.check_tool_policy("run_remote_command")

    assert decision["level"] == "dangerous"
    assert decision["decision"] == "approval_required"


def test_read_only_tool_is_allowed(monkeypatch):
    monkeypatch.setattr(
        tool_policy,
        "load_tool_policy",
        lambda: {"tools": {"get_current_time": {"level": "read_only"}}},
    )

    decision = tool_policy.check_tool_policy("get_current_time")

    assert decision["decision"] == "allow"
