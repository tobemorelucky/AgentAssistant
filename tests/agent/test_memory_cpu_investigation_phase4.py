import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODELS_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "models.py"
PROFILES_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "profiles.py"
EVIDENCE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "evidence.py"
MEMORY_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "memory_engine.py"
CPU_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "cpu_engine.py"
RUNTIME_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "runtime.py"
PATROL_DISPATCH_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "patrol_dispatch.py"
DISK_CLEANUP_PATH = ROOT / "app" / "agent" / "aiops" / "disk_cleanup.py"


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
memory_engine = _load_module("app.agent.aiops.investigation.memory_engine", MEMORY_ENGINE_PATH)
cpu_engine = _load_module("app.agent.aiops.investigation.cpu_engine", CPU_ENGINE_PATH)
runtime = _load_module("app.agent.aiops.investigation.runtime", RUNTIME_PATH)
patrol_dispatch = _load_module("app.agent.aiops.investigation.patrol_dispatch", PATROL_DISPATCH_PATH)


def _state(input_text: str, profile_id: str, store: dict[str, dict]):
    return {
        "input": input_text,
        "selected_profile": {"profile_id": profile_id},
        "evidence_store": store,
        "investigation_round": 1,
        "no_progress_rounds": 0,
    }


def test_runtime_registry_exposes_memory_and_cpu_runtimes():
    memory_runtime = runtime.get_runtime("memory_pressure_profile")
    cpu_runtime = runtime.get_runtime("cpu_pressure_profile")

    assert memory_runtime is not None
    assert cpu_runtime is not None
    assert [task["tool"] for task in memory_runtime.build_initial_tasks({})] == [
        "get_memory_summary",
        "list_top_memory_processes",
        "retrieve_knowledge",
    ]
    assert [task["tool"] for task in cpu_runtime.build_initial_tasks({})] == [
        "get_cpu_summary",
        "list_top_cpu_processes",
        "retrieve_knowledge",
    ]


def test_memory_profile_report_is_evidence_grounded():
    store = evidence.build_evidence_store(profiles.get_profile("memory_pressure_profile"))
    memory_engine.update_memory_evidence_store(
        store,
        slot="memory_summary",
        tool_name="get_memory_summary",
        raw_result={
            "ok": True,
            "host": "vm-01",
            "usage_percent": 76.4,
            "used_gb": 12.2,
            "total_gb": 16.0,
            "available_gb": 3.8,
            "source": "remote_host",
        },
    )
    memory_engine.update_memory_evidence_store(
        store,
        slot="top_memory_processes",
        tool_name="list_top_memory_processes",
        raw_result={
            "ok": True,
            "processes": [
                {"pid": 1001, "process_name": "python", "memory_percent": 18.4, "rss_gb": 2.1},
                {"pid": 1002, "process_name": "redis-server", "memory_percent": 11.2, "rss_gb": 1.3},
            ],
            "source": "remote_host",
        },
    )

    report = memory_engine.build_memory_investigation_report(
        _state("系统现在内存情况如何？", "memory_pressure_profile", store)
    )
    assert "AIOps 内存诊断报告" in report
    assert "76.4%" in report
    assert "python" in report
    assert "Runbook 仅作为参考" in report


def test_cpu_profile_finalizes_with_limitations_when_required_evidence_keeps_failing():
    store = evidence.build_evidence_store(profiles.get_profile("cpu_pressure_profile"))
    for _ in range(2):
        cpu_engine.update_cpu_evidence_store(
            store,
            slot="cpu_summary",
            tool_name="get_cpu_summary",
            raw_result={"ok": False, "message": "unavailable", "source": "remote_host"},
        )
        cpu_engine.update_cpu_evidence_store(
            store,
            slot="top_cpu_processes",
            tool_name="list_top_cpu_processes",
            raw_result={"ok": False, "message": "unsupported", "source": "remote_host"},
        )

    decision = cpu_engine.decide_cpu_stop(
        {
            **_state("系统现在 CPU 情况如何？", "cpu_pressure_profile", store),
            "investigation_round": 2,
            "no_progress_rounds": 1,
        }
    )
    assert decision.decision == models.StopDecisionType.FINALIZE_WITH_LIMITATIONS
    assert set(decision.missing_slots) == {"cpu_summary", "top_cpu_processes"}


def test_patrol_dispatch_maps_cpu_and_memory_alerts():
    cpu_alert = {"alert_name": "HighCPUUsage", "service_name": "svc-a", "severity": "critical"}
    memory_alert = {"alert_name": "MemoryPressure", "service_name": "svc-b", "severity": "high"}

    assert patrol_dispatch.resolve_alert_profile_id(cpu_alert) == "cpu_pressure_profile"
    assert patrol_dispatch.resolve_alert_profile_id(memory_alert) == "memory_pressure_profile"
