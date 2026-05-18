import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL_POLICY_PATH = ROOT / "app" / "agent" / "aiops" / "tool_policy.py"


def _load_module(module_name: str, path: Path):
    spec = spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


sys.modules.setdefault("app", types.ModuleType("app"))
sys.modules["app"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.agent", types.ModuleType("app.agent"))
sys.modules["app.agent"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.agent.aiops", types.ModuleType("app.agent.aiops"))
sys.modules["app.agent.aiops"].__path__ = []  # type: ignore[attr-defined]


tool_policy = _load_module("app.agent.aiops.tool_policy", TOOL_POLICY_PATH)


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


def test_phase4_runtime_tools_are_not_blocked(monkeypatch):
    monkeypatch.setattr(
        tool_policy,
        "load_tool_policy",
        lambda: {
            "tools": {
                "get_patrol_alerts": {"level": "read_only"},
                "get_cpu_summary": {"level": "read_only"},
                "list_top_cpu_processes": {"level": "read_only"},
                "get_memory_summary": {"level": "read_only"},
                "list_top_memory_processes": {"level": "read_only"},
            }
        },
    )

    for tool_name in [
        "get_patrol_alerts",
        "get_cpu_summary",
        "list_top_cpu_processes",
        "get_memory_summary",
        "list_top_memory_processes",
    ]:
        decision = tool_policy.check_tool_policy(tool_name)
        assert decision["decision"] == "allow"
