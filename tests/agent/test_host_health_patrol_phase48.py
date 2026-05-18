import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODELS_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "models.py"
PROFILES_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "profiles.py"
EVIDENCE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "evidence.py"
DISK_CLEANUP_PATH = ROOT / "app" / "agent" / "aiops" / "disk_cleanup.py"
CPU_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "cpu_engine.py"
MEMORY_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "memory_engine.py"
HOST_HEALTH_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "host_health_engine.py"


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
sys.modules.setdefault("app.agent.aiops.investigation", types.ModuleType("app.agent.aiops.investigation"))
sys.modules["app.agent.aiops.investigation"].__path__ = []  # type: ignore[attr-defined]


models = _load_module("app.agent.aiops.investigation.models", MODELS_PATH)
profiles = _load_module("app.agent.aiops.investigation.profiles", PROFILES_PATH)
evidence = _load_module("app.agent.aiops.investigation.evidence", EVIDENCE_PATH)
disk_cleanup = _load_module("app.agent.aiops.disk_cleanup", DISK_CLEANUP_PATH)
cpu_engine = _load_module("app.agent.aiops.investigation.cpu_engine", CPU_ENGINE_PATH)
memory_engine = _load_module("app.agent.aiops.investigation.memory_engine", MEMORY_ENGINE_PATH)
host_health_engine = _load_module("app.agent.aiops.investigation.host_health_engine", HOST_HEALTH_ENGINE_PATH)


def _state(store: dict[str, dict], input_text: str = "请开始一次 AIOps 巡检，并保留完整 Agent Trace。"):
    return {
        "input": input_text,
        "selected_profile": {"profile_id": "host_health_patrol_profile"},
        "evidence_store": store,
        "investigation_round": 1,
        "no_progress_rounds": 0,
    }


def test_host_health_profile_report_shows_healthy_summary():
    store = evidence.build_evidence_store(profiles.get_profile("host_health_patrol_profile"))
    host_health_engine.update_host_health_evidence_store(
        store,
        slot="cpu_summary",
        tool_name="get_cpu_summary",
        raw_result={
            "ok": True,
            "host": "he-VMware-Virtual-Platform",
            "usage_percent": 32.1,
            "load_1": 0.4,
            "load_5": 0.5,
            "load_15": 0.6,
            "status": "healthy",
            "source": "remote_host",
        },
    )
    host_health_engine.update_host_health_evidence_store(
        store,
        slot="memory_summary",
        tool_name="get_memory_summary",
        raw_result={
            "ok": True,
            "host": "he-VMware-Virtual-Platform",
            "usage_percent": 22.0,
            "used_gb": 1.7,
            "total_gb": 8.0,
            "available_gb": 6.0,
            "status": "healthy",
            "source": "remote_host",
        },
    )
    host_health_engine.update_host_health_evidence_store(
        store,
        slot="disk_usage",
        tool_name="get_disk_usage",
        raw_result={
            "ok": True,
            "host": "he-VMware-Virtual-Platform",
            "mount": "/",
            "usage_percent": 41.1,
            "used_gb": 16.05,
            "total_gb": 39.07,
            "available_gb": 21.01,
            "status": "healthy",
            "source": "remote_host",
        },
    )
    host_health_engine.update_host_health_evidence_store(
        store,
        slot="active_alerts",
        tool_name="get_patrol_alerts",
        raw_result={"ok": True, "provider": "remote_host", "active_alerts": [], "source": "remote_host"},
    )
    report = host_health_engine.build_host_health_patrol_report(_state(store))
    assert "当前未发现明显资源级异常" in report
    assert "32.1%" in report
    assert "22.0%" in report
    assert "41.1%" in report
    assert "当前未发现活跃主机级告警" in report


def test_host_health_profile_finishes_with_limitations_when_required_tools_fail():
    store = evidence.build_evidence_store(profiles.get_profile("host_health_patrol_profile"))
    for _ in range(2):
        host_health_engine.update_host_health_evidence_store(
            store,
            slot="cpu_summary",
            tool_name="get_cpu_summary",
            raw_result={"error": "Tool not found: get_cpu_summary"},
        )
        host_health_engine.update_host_health_evidence_store(
            store,
            slot="memory_summary",
            tool_name="get_memory_summary",
            raw_result={"error": "Tool not found: get_memory_summary"},
        )
        host_health_engine.update_host_health_evidence_store(
            store,
            slot="disk_usage",
            tool_name="get_disk_usage",
            raw_result={"ok": False, "message": "disk unavailable", "source": "remote_host"},
        )
    decision = host_health_engine.decide_host_health_stop(
        {**_state(store), "investigation_round": 2, "no_progress_rounds": 1}
    )
    assert decision.decision == models.StopDecisionType.FINALIZE_WITH_LIMITATIONS
    report = host_health_engine.build_host_health_patrol_report(_state(store))
    assert "未成功获取 CPU 实时摘要" in report
    assert "未成功获取内存实时摘要" in report
    assert "未成功获取磁盘实时摘要" in report
