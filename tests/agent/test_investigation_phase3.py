import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODELS_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "models.py"
PROFILES_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "profiles.py"
EVIDENCE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "evidence.py"
DISK_CLEANUP_PATH = ROOT / "app" / "agent" / "aiops" / "disk_cleanup.py"
DISK_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "disk_engine.py"
HOST_HEALTH_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "host_health_engine.py"
MEMORY_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "memory_engine.py"
CPU_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "cpu_engine.py"
RUNTIME_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "runtime.py"
PATROL_DISPATCH_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "patrol_dispatch.py"
UTILS_PATH = ROOT / "app" / "agent" / "aiops" / "utils.py"


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
sys.modules.setdefault("langchain_core", types.ModuleType("langchain_core"))
sys.modules["langchain_core"].__path__ = []  # type: ignore[attr-defined]
documents_module = types.ModuleType("langchain_core.documents")


class _Document:
    def __init__(self, page_content: str, metadata: dict | None = None):
        self.page_content = page_content
        self.metadata = metadata or {}


documents_module.Document = _Document  # type: ignore[attr-defined]
sys.modules["langchain_core.documents"] = documents_module

utils = _load_module("app.agent.aiops.utils", UTILS_PATH)


models = _load_module("app.agent.aiops.investigation.models", MODELS_PATH)
profiles = _load_module("app.agent.aiops.investigation.profiles", PROFILES_PATH)
evidence = _load_module("app.agent.aiops.investigation.evidence", EVIDENCE_PATH)
disk_cleanup = _load_module("app.agent.aiops.disk_cleanup", DISK_CLEANUP_PATH)
disk_engine = _load_module("app.agent.aiops.investigation.disk_engine", DISK_ENGINE_PATH)
memory_engine = _load_module("app.agent.aiops.investigation.memory_engine", MEMORY_ENGINE_PATH)
cpu_engine = _load_module("app.agent.aiops.investigation.cpu_engine", CPU_ENGINE_PATH)
host_health_engine = _load_module("app.agent.aiops.investigation.host_health_engine", HOST_HEALTH_ENGINE_PATH)
runtime = _load_module("app.agent.aiops.investigation.runtime", RUNTIME_PATH)
patrol_dispatch = _load_module("app.agent.aiops.investigation.patrol_dispatch", PATROL_DISPATCH_PATH)


def test_runtime_registry_exposes_disk_runtime():
    disk_runtime = runtime.get_runtime("disk_pressure_profile")
    assert disk_runtime is not None
    tasks = disk_runtime.build_initial_tasks({})
    assert [task["tool"] for task in tasks] == [
        "get_disk_usage",
        "list_large_directories",
        "list_large_files",
        "retrieve_knowledge",
    ]


def test_default_profile_is_host_health_profile():
    profile = profiles.resolve_selected_profile(mode="default", matched_skills=[])
    assert profile is not None
    assert profile.profile_id == "host_health_patrol_profile"
    store = evidence.build_evidence_store(profile)
    assert set(store) == {"cpu_summary", "memory_summary", "disk_usage", "active_alerts"}


def test_runtime_registry_exposes_host_health_runtime():
    host_runtime = runtime.get_runtime("host_health_patrol_profile")
    assert host_runtime is not None
    tasks = host_runtime.build_initial_tasks({})
    assert [task["tool"] for task in tasks] == [
        "get_cpu_summary",
        "get_memory_summary",
        "get_disk_usage",
        "get_patrol_alerts",
    ]


def test_patrol_dispatch_maps_disk_alert_to_disk_profile():
    target_alert = {
        "alert_name": "HighDiskUsage",
        "service_name": "data-sync-service",
        "severity": "critical",
    }
    assert patrol_dispatch.resolve_alert_profile_id(target_alert) == "disk_pressure_profile"
    selected = patrol_dispatch.select_target_alert(
        [
            {"alert_name": "HighCPUUsage", "service_name": "svc-a", "severity": "high"},
            target_alert,
        ]
    )
    assert selected == target_alert


def test_patrol_dispatch_stops_cleanly_for_unsupported_alert_profile():
    target_alert = {
        "alert_name": "HighNetworkLatency",
        "service_name": "compute-service",
        "severity": "critical",
    }
    assert patrol_dispatch.resolve_alert_profile_id(target_alert) is None
    report = patrol_dispatch.build_unsupported_profile_report(target_alert)
    assert "HighNetworkLatency" in report
    assert "尚未实现对应的结构化 Investigation Profile" in report
