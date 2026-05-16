import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODELS_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "models.py"
PROFILES_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "profiles.py"
EVIDENCE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "evidence.py"
DISK_CLEANUP_PATH = ROOT / "app" / "agent" / "aiops" / "disk_cleanup.py"
DISK_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "disk_engine.py"
RUNTIME_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "runtime.py"
PATROL_DISPATCH_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "patrol_dispatch.py"


def _load_module(module_name: str, path: Path):
    spec = spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


models = _load_module("app.agent.aiops.investigation.models", MODELS_PATH)
profiles = _load_module("app.agent.aiops.investigation.profiles", PROFILES_PATH)
evidence = _load_module("app.agent.aiops.investigation.evidence", EVIDENCE_PATH)
disk_cleanup = _load_module("app.agent.aiops.disk_cleanup", DISK_CLEANUP_PATH)
disk_engine = _load_module("app.agent.aiops.investigation.disk_engine", DISK_ENGINE_PATH)
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


def test_default_profile_is_dispatcher_profile():
    profile = profiles.resolve_selected_profile(mode="default", matched_skills=[])
    assert profile is not None
    assert profile.profile_id == "patrol_dispatch_profile"
    store = evidence.build_evidence_store(profile)
    assert set(store) == {"active_alerts", "target_alert"}


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
        "alert_name": "HighCPUUsage",
        "service_name": "compute-service",
        "severity": "critical",
    }
    assert patrol_dispatch.resolve_alert_profile_id(target_alert) is None
    report = patrol_dispatch.build_unsupported_profile_report(target_alert)
    assert "尚未实现" in report
    assert "没有回退到旧的 patrol 深诊断模板链" in report
