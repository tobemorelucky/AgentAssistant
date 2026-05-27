from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _ensure_package(name: str) -> None:
    if name in sys.modules:
        return
    module = types.ModuleType(name)
    module.__path__ = []  # type: ignore[attr-defined]
    sys.modules[name] = module


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


for package in [
    "app",
    "app.agent",
    "app.agent.aiops",
    "app.agent.aiops.remediation",
    "app.monitoring",
    "app.services",
]:
    _ensure_package(package)


config_module = types.ModuleType("app.config")
config_module.config = types.SimpleNamespace(
    aiops_heartbeat_enabled=False,
    aiops_heartbeat_interval_minutes=60,
    aiops_heartbeat_trigger_deep_diagnosis=True,
    aiops_heartbeat_store_report=True,
    aiops_heartbeat_max_concurrent_runs=1,
)
sys.modules["app.config"] = config_module

runtime_store_stub = types.ModuleType("app.agent.aiops.runtime_store")


class _RuntimeStore:
    def __init__(self) -> None:
        self.latest = {}
        self.history = []
        self.audit = []

    def save_heartbeat_record(self, record, max_items: int = 50):
        self.latest = record
        self.history.append(record)
        self.history = self.history[-max_items:]

    def load_latest_heartbeat(self):
        return self.latest

    def load_heartbeat_history(self, limit: int = 20):
        return self.history[-limit:]

    def append_audit_event(self, event_type: str, payload):
        self.audit.append((event_type, payload))


runtime_store_stub.runtime_store = _RuntimeStore()
sys.modules["app.agent.aiops.runtime_store"] = runtime_store_stub

alert_provider_stub = types.ModuleType("app.monitoring.alert_provider")
alert_provider_stub.build_disabled_alert_result = lambda: {"active_alerts": [], "provider": "disabled", "message": "", "total": 0}
alert_provider_stub.build_remote_host_alerts = lambda: {"active_alerts": [], "provider": "remote_host", "message": "", "total": 0}
alert_provider_stub.get_alert_provider_name = lambda: "remote_host"
sys.modules["app.monitoring.alert_provider"] = alert_provider_stub

monitor_provider_stub = types.ModuleType("app.monitoring.monitor_provider")
monitor_provider_stub.get_cpu_summary_data = lambda: {"ok": True, "host": "healthy-host", "status": "healthy", "usage_percent": 20.0}
monitor_provider_stub.get_memory_summary_data = lambda: {"ok": True, "host": "healthy-host", "status": "healthy", "usage_percent": 30.0}
monitor_provider_stub.get_disk_usage_data = lambda mount="/": {"ok": True, "host": "healthy-host", "status": "healthy", "usage_percent": 40.0}
monitor_provider_stub.dry_run_remediation_action = lambda action_id, params=None: {
    "ok": True,
    "action_id": action_id,
    "estimated_reclaim_gb": 1.5,
    "affected_files": 12,
    "sample_paths": ["/tmp/mock-file-1"],
    "source": "mock",
}
sys.modules["app.monitoring.monitor_provider"] = monitor_provider_stub

aiops_service_stub = types.ModuleType("app.services.aiops_service")
aiops_service_stub.DEFAULT_AIOPS_TASK = "default heartbeat task"


class _AIOpsService:
    async def run_diagnosis_once(self, **kwargs):
        return {"completed": True, "state": {}, "event": {}, "status": "completed"}


aiops_service_stub.aiops_service = _AIOpsService()
sys.modules["app.services.aiops_service"] = aiops_service_stub

action_schema = _load_module(
    "app.agent.aiops.remediation.action_schema",
    ROOT / "app" / "agent" / "aiops" / "remediation" / "action_schema.py",
)
action_registry = _load_module(
    "app.agent.aiops.remediation.action_registry",
    ROOT / "app" / "agent" / "aiops" / "remediation" / "action_registry.py",
)
action_policy = _load_module(
    "app.agent.aiops.remediation.action_policy",
    ROOT / "app" / "agent" / "aiops" / "remediation" / "action_policy.py",
)
candidate_builder = _load_module(
    "app.agent.aiops.remediation.candidate_builder",
    ROOT / "app" / "agent" / "aiops" / "remediation" / "candidate_builder.py",
)
heartbeat_module = _load_module(
    "app.agent.aiops.heartbeat",
    ROOT / "app" / "agent" / "aiops" / "heartbeat.py",
)


def test_disk_profile_builds_remediation_candidates():
    state = {
        "selected_profile": {"profile_id": "disk_pressure_profile"},
        "target_alert": {"alert_name": "HighDiskUsage", "severity": "critical"},
    }
    candidates = candidate_builder.build_remediation_candidates(state)
    action_ids = {item["action_id"] for item in candidates}
    assert "cleanup_tmp_old_files" in action_ids
    assert "docker_builder_prune" in action_ids
    assert "vacuum_journal_logs" in action_ids


def test_execute_without_approval_is_rejected():
    decision = action_policy.evaluate_action_policy("restart_service", approval_token="")
    assert decision["allowed"] is False
    assert decision["decision"] == "approval_required"


def test_forbidden_action_is_rejected():
    decision = action_policy.evaluate_action_policy("reboot_server", approval_token="token")
    assert decision["allowed"] is False
    assert decision["decision"] == "reject"


def test_mock_dry_run_returns_estimate():
    result = monitor_provider_stub.dry_run_remediation_action("cleanup_tmp_old_files", {"path": "/tmp"})
    assert result["ok"] is True
    assert "estimated_reclaim_gb" in result


def test_healthy_heartbeat_only_saves_summary():
    manager = heartbeat_module.HeartbeatPatrolManager()
    record = asyncio.run(manager.run_once(trigger="manual", session_id="heartbeat-test-healthy"))
    assert record["overall_status"] == "healthy"
    assert record["diagnosis_report_summary"] == ""


def test_abnormal_heartbeat_triggers_deep_diagnosis(monkeypatch):
    manager = heartbeat_module.HeartbeatPatrolManager()
    monkeypatch.setattr(
        heartbeat_module,
        "get_cpu_summary_data",
        lambda: {"ok": True, "host": "demo-server-01", "status": "warning", "usage_percent": 88.7},
    )
    monkeypatch.setattr(
        heartbeat_module,
        "get_memory_summary_data",
        lambda: {"ok": True, "host": "demo-server-01", "status": "warning", "usage_percent": 86.3},
    )
    monkeypatch.setattr(
        heartbeat_module,
        "get_disk_usage_data",
        lambda mount="/": {"ok": True, "host": "demo-server-01", "status": "critical", "usage_percent": 92.4},
    )
    monkeypatch.setattr(
        heartbeat_module,
        "_alert_payload",
        lambda: {
            "active_alerts": [{"alert_name": "HighCPUUsage", "severity": "critical", "service_name": "data-sync-service"}],
            "provider": "mock",
            "message": "",
            "total": 1,
        },
    )

    async def _fake_run_diagnosis_once(**kwargs):
        return {
            "completed": True,
            "state": {
                "selected_profile": {"profile_id": "cpu_pressure_profile"},
                "target_alert": {"alert_name": "HighCPUUsage", "severity": "critical", "service_name": "data-sync-service"},
                "response": "CPU diagnosis report",
            },
            "event": {},
            "status": "completed",
        }

    monkeypatch.setattr(heartbeat_module.aiops_service, "run_diagnosis_once", _fake_run_diagnosis_once)
    record = asyncio.run(manager.run_once(trigger="manual", session_id="heartbeat-test-abnormal"))
    assert record["overall_status"] == "abnormal"
    assert record["selected_profile"] == "cpu_pressure_profile"
    assert record["remediation_candidates"]
