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
DISK_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "disk_engine.py"
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
patrol_dispatch = _load_module("app.agent.aiops.investigation.patrol_dispatch", PATROL_DISPATCH_PATH)
disk_engine = _load_module("app.agent.aiops.investigation.disk_engine", DISK_ENGINE_PATH)
memory_engine = _load_module("app.agent.aiops.investigation.memory_engine", MEMORY_ENGINE_PATH)
cpu_engine = _load_module("app.agent.aiops.investigation.cpu_engine", CPU_ENGINE_PATH)
host_health_engine = _load_module("app.agent.aiops.investigation.host_health_engine", HOST_HEALTH_ENGINE_PATH)
runtime = _load_module("app.agent.aiops.investigation.runtime", RUNTIME_PATH)


def _state(input_text: str, profile_id: str, store: dict[str, dict], **extra):
    return {
        "input": input_text,
        "selected_profile": {"profile_id": profile_id},
        "evidence_store": store,
        "investigation_round": 1,
        "no_progress_rounds": 0,
        **extra,
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


def test_cpu_summary_error_payload_is_failed():
    store = evidence.build_evidence_store(profiles.get_profile("cpu_pressure_profile"))
    cpu_engine.update_cpu_evidence_store(
        store,
        slot="cpu_summary",
        tool_name="get_cpu_summary",
        raw_result={"error": "Tool not found: get_cpu_summary"},
    )
    assert store["cpu_summary"]["status"] == models.EvidenceStatus.FAILED
    assert "Tool not found" in store["cpu_summary"]["error_message"]


def test_top_cpu_processes_error_payload_is_failed():
    store = evidence.build_evidence_store(profiles.get_profile("cpu_pressure_profile"))
    cpu_engine.update_cpu_evidence_store(
        store,
        slot="top_cpu_processes",
        tool_name="list_top_cpu_processes",
        raw_result={"error": "Tool not found: list_top_cpu_processes"},
    )
    assert store["top_cpu_processes"]["status"] == models.EvidenceStatus.FAILED


def test_cpu_report_with_missing_required_evidence_shows_gaps():
    store = evidence.build_evidence_store(profiles.get_profile("cpu_pressure_profile"))
    cpu_engine.update_cpu_evidence_store(
        store,
        slot="cpu_summary",
        tool_name="get_cpu_summary",
        raw_result={"error": "Tool not found: get_cpu_summary"},
    )
    cpu_engine.update_cpu_evidence_store(
        store,
        slot="top_cpu_processes",
        tool_name="list_top_cpu_processes",
        raw_result={"error": "Tool not found: list_top_cpu_processes"},
    )
    report = cpu_engine.build_cpu_investigation_report(
        _state("系统现在 CPU 情况如何？", "cpu_pressure_profile", store)
    )
    assert "未成功获取实时 CPU 摘要" in report
    assert "未成功获取热点 CPU 进程列表" in report
    assert "当前关键证据已覆盖第一版 CPU Profile 所需范围" not in report


def test_memory_summary_error_payload_is_failed():
    store = evidence.build_evidence_store(profiles.get_profile("memory_pressure_profile"))
    memory_engine.update_memory_evidence_store(
        store,
        slot="memory_summary",
        tool_name="get_memory_summary",
        raw_result={"error": "Tool not found: get_memory_summary"},
    )
    assert store["memory_summary"]["status"] == models.EvidenceStatus.FAILED
    assert "Tool not found" in store["memory_summary"]["error_message"]


def test_top_memory_processes_error_payload_is_failed():
    store = evidence.build_evidence_store(profiles.get_profile("memory_pressure_profile"))
    memory_engine.update_memory_evidence_store(
        store,
        slot="top_memory_processes",
        tool_name="list_top_memory_processes",
        raw_result={"error": "Tool not found: list_top_memory_processes"},
    )
    assert store["top_memory_processes"]["status"] == models.EvidenceStatus.FAILED


def test_memory_report_with_missing_required_evidence_shows_gaps():
    store = evidence.build_evidence_store(profiles.get_profile("memory_pressure_profile"))
    memory_engine.update_memory_evidence_store(
        store,
        slot="memory_summary",
        tool_name="get_memory_summary",
        raw_result={"error": "Tool not found: get_memory_summary"},
    )
    memory_engine.update_memory_evidence_store(
        store,
        slot="top_memory_processes",
        tool_name="list_top_memory_processes",
        raw_result={"error": "Tool not found: list_top_memory_processes"},
    )
    report = memory_engine.build_memory_investigation_report(
        _state("系统现在内存情况如何？", "memory_pressure_profile", store)
    )
    assert "未成功获取实时内存摘要" in report
    assert "未成功获取热点内存进程列表" in report
    assert "当前关键证据已覆盖第一版 Memory Profile 所需范围" not in report


def test_memory_profile_report_is_evidence_grounded_with_real_values():
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
    assert "AIOps 内存专项诊断报告" in report
    assert "76.4%" in report
    assert "python" in report


def test_cpu_profile_can_finalize_with_real_results():
    store = evidence.build_evidence_store(profiles.get_profile("cpu_pressure_profile"))
    cpu_engine.update_cpu_evidence_store(
        store,
        slot="cpu_summary",
        tool_name="get_cpu_summary",
        raw_result={
            "ok": True,
            "host": "vm-01",
            "usage_percent": 61.5,
            "cores": 8,
            "load_1": 1.2,
            "load_5": 0.9,
            "load_15": 0.8,
            "source": "remote_host",
        },
    )
    cpu_engine.update_cpu_evidence_store(
        store,
        slot="top_cpu_processes",
        tool_name="list_top_cpu_processes",
        raw_result={
            "ok": True,
            "processes": [
                {"pid": 2001, "process_name": "python", "cpu_percent": 38.4},
                {"pid": 2002, "process_name": "nginx", "cpu_percent": 11.2},
            ],
            "source": "remote_host",
        },
    )
    cpu_engine.update_cpu_evidence_store(
        store,
        slot="cpu_runbook",
        tool_name="retrieve_knowledge",
        raw_result={"ok": True, "content": "CPU 排查 runbook", "source": "local_rag"},
    )
    decision = cpu_engine.decide_cpu_stop(
        {
            **_state("系统现在 CPU 情况如何？", "cpu_pressure_profile", store),
            "investigation_round": 1,
            "no_progress_rounds": 0,
        }
    )
    assert decision.decision == models.StopDecisionType.FINALIZE


def test_cpu_profile_finalizes_with_limitations_when_required_evidence_keeps_failing():
    store = evidence.build_evidence_store(profiles.get_profile("cpu_pressure_profile"))
    for _ in range(2):
        cpu_engine.update_cpu_evidence_store(
            store,
            slot="cpu_summary",
            tool_name="get_cpu_summary",
            raw_result={"error": "Tool not found: get_cpu_summary"},
        )
        cpu_engine.update_cpu_evidence_store(
            store,
            slot="top_cpu_processes",
            tool_name="list_top_cpu_processes",
            raw_result={"error": "Tool not found: list_top_cpu_processes"},
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
    cpu_alert = {"alert_name": "HighCPUUsage", "severity": "critical"}
    memory_alert = {"alert_name": "MemoryPressure", "severity": "high"}

    assert patrol_dispatch.resolve_alert_profile_id(cpu_alert) == "cpu_pressure_profile"
    assert patrol_dispatch.resolve_alert_profile_id(memory_alert) == "memory_pressure_profile"


def test_cpu_follow_up_triggers_web_search_only_when_rag_missing():
    store = evidence.build_evidence_store(profiles.get_profile("cpu_pressure_profile"))
    cpu_engine.update_cpu_evidence_store(
        store,
        slot="cpu_summary",
        tool_name="get_cpu_summary",
        raw_result={"ok": True, "host": "vm-01", "usage_percent": 83.4, "status": "warning", "source": "remote_host"},
    )
    cpu_engine.update_cpu_evidence_store(
        store,
        slot="top_cpu_processes",
        tool_name="list_top_cpu_processes",
        raw_result={"ok": True, "processes": [{"process_name": "uvicorn", "cpu_percent": 48.0}], "source": "remote_host"},
    )
    cpu_engine.update_cpu_evidence_store(
        store,
        slot="cpu_runbook",
        tool_name="retrieve_knowledge",
        raw_result={"ok": True, "content": "", "documents": [], "source": "local_rag"},
    )
    tasks = cpu_engine.build_follow_up_tasks(
        _state("CPU 占用高怎么办？", "cpu_pressure_profile", store)
    )
    assert any(task["tool"] == "web_search" for task in tasks)


def test_memory_follow_up_triggers_web_search_when_feedback_failed():
    store = evidence.build_evidence_store(profiles.get_profile("memory_pressure_profile"))
    memory_engine.update_memory_evidence_store(
        store,
        slot="memory_summary",
        tool_name="get_memory_summary",
        raw_result={"ok": True, "host": "vm-01", "usage_percent": 88.0, "status": "warning", "source": "remote_host"},
    )
    memory_engine.update_memory_evidence_store(
        store,
        slot="top_memory_processes",
        tool_name="list_top_memory_processes",
        raw_result={"ok": True, "processes": [{"process_name": "python", "memory_percent": 35.0}], "source": "remote_host"},
    )
    memory_engine.update_memory_evidence_store(
        store,
        slot="memory_runbook",
        tool_name="retrieve_knowledge",
        raw_result={"ok": True, "content": "已有建议", "documents": [{"title": "runbook"}], "source": "local_rag"},
    )
    tasks = memory_engine.build_follow_up_tasks(
        _state(
            "我按你说的做了，还是没效果，继续查。",
            "memory_pressure_profile",
            store,
            remediation_feedback_failed=True,
        )
    )
    assert any(task["tool"] == "web_search" for task in tasks)
