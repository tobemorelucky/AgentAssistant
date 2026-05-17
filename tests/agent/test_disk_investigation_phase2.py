import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODELS_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "models.py"
PROFILES_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "profiles.py"
EVIDENCE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "evidence.py"
DISK_ENGINE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "disk_engine.py"
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
disk_engine = _load_module("app.agent.aiops.investigation.disk_engine", DISK_ENGINE_PATH)


def _state_with_store(store):
    return {
        "input": "请检查服务器当前磁盘空间使用情况，并分析主要占用来源。",
        "selected_profile": {"profile_id": "disk_pressure_profile"},
        "evidence_store": store,
        "investigation_round": 1,
        "no_progress_rounds": 0,
    }


def test_disk_pressure_profile_slots_and_initial_tasks():
    profile = profiles.get_profile("disk_pressure_profile")
    assert profile is not None
    assert profile.required_evidence_slots == ["disk_usage", "large_directories", "large_files"]
    assert profile.conditional_evidence_slots == ["docker_disk_usage", "deleted_open_files"]
    assert profile.reference_evidence_slots == ["disk_runbook"]

    tasks = disk_engine.build_initial_disk_tasks()
    assert [task["tool"] for task in tasks] == [
        "get_disk_usage",
        "list_large_directories",
        "list_large_files",
        "retrieve_knowledge",
    ]


def test_disk_follow_up_adds_docker_when_directory_points_to_docker():
    store = evidence.build_evidence_store(profiles.get_profile("disk_pressure_profile"))
    disk_engine.update_disk_evidence_store(
        store,
        slot="disk_usage",
        tool_name="get_disk_usage",
        raw_result={"usage_percent": 85.0, "used_gb": 85, "total_gb": 100, "available_gb": 15, "host": "vm", "mount": "/", "source": "remote_host"},
    )
    disk_engine.update_disk_evidence_store(
        store,
        slot="large_directories",
        tool_name="list_large_directories",
        raw_result={"directories": [{"path": "/var/lib/docker", "size_gb": 30}], "source": "remote_host"},
    )
    disk_engine.update_disk_evidence_store(
        store,
        slot="large_files",
        tool_name="list_large_files",
        raw_result={"files": [{"path": "/swap.img", "size_gb": 4.0}], "source": "remote_host"},
    )
    disk_engine.update_disk_evidence_store(
        store,
        slot="disk_runbook",
        tool_name="retrieve_knowledge",
        raw_result={"content": "disk runbook", "source": "mock"},
    )

    tasks = disk_engine.build_follow_up_tasks(_state_with_store(store))
    assert tasks
    assert tasks[0]["slot"] == "docker_disk_usage"


def test_disk_stop_finalizes_with_limitations_after_required_failures_exhausted():
    store = evidence.build_evidence_store(profiles.get_profile("disk_pressure_profile"))
    for _ in range(2):
        disk_engine.update_disk_evidence_store(
            store,
            slot="disk_usage",
            tool_name="get_disk_usage",
            raw_result={"usage_percent": 85.0, "used_gb": 85, "total_gb": 100, "available_gb": 15, "host": "vm", "mount": "/", "source": "remote_host"},
        )
        disk_engine.update_disk_evidence_store(
            store,
            slot="large_directories",
            tool_name="list_large_directories",
            raw_result={"ok": False, "message": "permission denied", "source": "remote_host"},
        )
        disk_engine.update_disk_evidence_store(
            store,
            slot="large_files",
            tool_name="list_large_files",
            raw_result={"ok": False, "message": "scan failed", "source": "remote_host"},
        )

    decision = disk_engine.decide_disk_stop(
        {
            **_state_with_store(store),
            "investigation_round": 2,
            "no_progress_rounds": 1,
        }
    )
    assert decision.decision == models.StopDecisionType.FINALIZE_WITH_LIMITATIONS
    assert set(decision.missing_slots) == {"large_directories", "large_files"}


def test_disk_report_marks_gaps_and_safety_when_conditional_evidence_missing():
    store = evidence.build_evidence_store(profiles.get_profile("disk_pressure_profile"))
    disk_engine.update_disk_evidence_store(
        store,
        slot="disk_usage",
        tool_name="get_disk_usage",
        raw_result={"usage_percent": 41.1, "used_gb": 16.05, "total_gb": 39.07, "available_gb": 21.01, "host": "he-VMware-Virtual-Platform", "mount": "/", "source": "remote_host"},
    )
    disk_engine.update_disk_evidence_store(
        store,
        slot="large_directories",
        tool_name="list_large_directories",
        raw_result={"directories": [{"path": "/usr", "size_gb": 6.1}, {"path": "/var", "size_gb": 5.8}], "source": "remote_host"},
    )
    disk_engine.update_disk_evidence_store(
        store,
        slot="large_files",
        tool_name="list_large_files",
        raw_result={
            "files": [{"path": "/swap.img", "size_gb": 4.0}],
            "scan_incomplete": True,
            "permission_denied_count": 120,
            "source": "remote_host",
        },
    )

    report = disk_engine.build_disk_investigation_report(_state_with_store(store))
    assert "he-VMware-Virtual-Platform" in report
    assert "41.1%" in report
    assert "当前未接入 Docker 额外证据" in report
    assert "当前未接入 deleted open files 额外证据" in report
    assert "本次扫描存在权限跳过，结果可能不完整" in report
    assert "没有执行任何删除" in report
