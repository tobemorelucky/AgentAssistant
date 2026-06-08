import asyncio
import json
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INCIDENT_MEMORY_PATH = ROOT / "app" / "agent" / "aiops" / "memory" / "incident_memory.py"
API_PATH = ROOT / "app" / "api" / "aiops.py"


def _load_incident_memory_module(enabled: bool = True, debug_api: bool = False):
    backups = {
        name: sys.modules.get(name)
        for name in [
            "app",
            "app.config",
            "app.agent",
            "app.agent.aiops",
            "app.agent.aiops.runtime_store",
            "loguru",
        ]
    }

    fake_app = types.ModuleType("app")
    fake_app.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app"] = fake_app
    fake_agent = types.ModuleType("app.agent")
    fake_agent.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.agent"] = fake_agent
    fake_aiops = types.ModuleType("app.agent.aiops")
    fake_aiops.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.agent.aiops"] = fake_aiops

    config_module = types.ModuleType("app.config")
    config_module.config = types.SimpleNamespace(
        aiops_incident_memory_enabled=enabled,
        aiops_incident_memory_debug_api=debug_api,
        aiops_incident_memory_top_k=3,
        debug=False,
    )
    sys.modules["app.config"] = config_module

    runtime_store_module = types.ModuleType("app.agent.aiops.runtime_store")
    runtime_store_module.INCIDENT_DIR = ROOT / "data" / "incident_memory"
    sys.modules["app.agent.aiops.runtime_store"] = runtime_store_module

    loguru_module = types.ModuleType("loguru")
    loguru_module.logger = types.SimpleNamespace(warning=lambda *a, **k: None)
    sys.modules["loguru"] = loguru_module

    try:
        spec = spec_from_file_location("test_incident_memory_phase7_module", INCIDENT_MEMORY_PATH)
        assert spec and spec.loader
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, config_module.config
    finally:
        for name, original in backups.items():
            if original is not None:
                sys.modules[name] = original
            else:
                sys.modules.pop(name, None)


def _load_aiops_api_module(incident_memory_module, config_obj):
    backups = {
        name: sys.modules.get(name)
        for name in [
            "app",
            "app.api",
            "app.agent",
            "app.agent.aiops",
            "app.agent.aiops.heartbeat",
            "app.agent.aiops.memory",
            "app.agent.aiops.memory.incident_memory",
            "app.agent.aiops.memory.session_memory",
            "app.agent.aiops.remediation",
            "app.agent.aiops.runtime_store",
            "app.models",
            "app.models.agent",
            "app.models.aiops",
            "app.monitoring",
            "app.monitoring.monitor_provider",
            "app.services",
            "app.services.aiops_service",
            "app.config",
            "loguru",
            "sse_starlette.sse",
            "fastapi",
        ]
    }

    fake_app = types.ModuleType("app")
    fake_app.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app"] = fake_app
    fake_api = types.ModuleType("app.api")
    fake_api.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.api"] = fake_api
    fake_agent = types.ModuleType("app.agent")
    fake_agent.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.agent"] = fake_agent
    fake_aiops = types.ModuleType("app.agent.aiops")
    fake_aiops.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.agent.aiops"] = fake_aiops
    fake_memory_pkg = types.ModuleType("app.agent.aiops.memory")
    fake_memory_pkg.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.agent.aiops.memory"] = fake_memory_pkg
    sys.modules["app.agent.aiops.memory.incident_memory"] = incident_memory_module

    session_memory_module = types.ModuleType("app.agent.aiops.memory.session_memory")
    session_memory_module.clear_session_memory = lambda session_id: {"session_id": session_id, "recent_turns": [], "turn_count": 0}
    session_memory_module.load_session_memory = lambda session_id: {"session_id": session_id, "recent_turns": [], "turn_count": 0}
    sys.modules["app.agent.aiops.memory.session_memory"] = session_memory_module

    heartbeat_module = types.ModuleType("app.agent.aiops.heartbeat")
    heartbeat_module.heartbeat_manager = types.SimpleNamespace(
        latest=lambda: {},
        history=lambda limit=20: [],
        run_once=lambda **kwargs: {},
    )
    sys.modules["app.agent.aiops.heartbeat"] = heartbeat_module

    remediation_module = types.ModuleType("app.agent.aiops.remediation")
    remediation_module.evaluate_action_policy = lambda *args, **kwargs: {"allowed": True}
    sys.modules["app.agent.aiops.remediation"] = remediation_module

    runtime_store_module = types.ModuleType("app.agent.aiops.runtime_store")
    runtime_store_module.runtime_store = types.SimpleNamespace(append_audit_event=lambda *args, **kwargs: None)
    sys.modules["app.agent.aiops.runtime_store"] = runtime_store_module

    models_pkg = types.ModuleType("app.models")
    models_pkg.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.models"] = models_pkg
    models_agent = types.ModuleType("app.models.agent")
    models_agent.HeartbeatRunRequest = type("HeartbeatRunRequest", (), {})
    models_agent.RemediationDryRunRequest = type("RemediationDryRunRequest", (), {})
    models_agent.RemediationExecuteRequest = type("RemediationExecuteRequest", (), {})
    sys.modules["app.models.agent"] = models_agent
    models_aiops = types.ModuleType("app.models.aiops")
    models_aiops.AIOpsRequest = type("AIOpsRequest", (), {})
    sys.modules["app.models.aiops"] = models_aiops

    monitoring_pkg = types.ModuleType("app.monitoring")
    monitoring_pkg.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.monitoring"] = monitoring_pkg
    monitor_provider_module = types.ModuleType("app.monitoring.monitor_provider")
    monitor_provider_module.dry_run_remediation_action = lambda *args, **kwargs: {"ok": True}
    monitor_provider_module.execute_remediation_action = lambda *args, **kwargs: {"ok": True}
    sys.modules["app.monitoring.monitor_provider"] = monitor_provider_module

    services_pkg = types.ModuleType("app.services")
    services_pkg.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.services"] = services_pkg
    services_aiops = types.ModuleType("app.services.aiops_service")
    services_aiops.DEFAULT_AIOPS_TASK = "default"
    services_aiops.aiops_service = types.SimpleNamespace()
    sys.modules["app.services.aiops_service"] = services_aiops

    config_module = types.ModuleType("app.config")
    config_module.config = config_obj
    sys.modules["app.config"] = config_module

    fastapi_module = types.ModuleType("fastapi")

    class _HTTPException(Exception):
        def __init__(self, status_code: int, detail=None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _APIRouter:
        def post(self, *_args, **_kwargs):
            return lambda func: func

        def get(self, *_args, **_kwargs):
            return lambda func: func

        def delete(self, *_args, **_kwargs):
            return lambda func: func

    fastapi_module.HTTPException = _HTTPException
    fastapi_module.APIRouter = _APIRouter
    sys.modules["fastapi"] = fastapi_module

    loguru_module = types.ModuleType("loguru")
    loguru_module.logger = types.SimpleNamespace(info=lambda *a, **k: None, error=lambda *a, **k: None)
    sys.modules["loguru"] = loguru_module

    sse_module = types.ModuleType("sse_starlette.sse")
    sse_module.EventSourceResponse = object
    sys.modules["sse_starlette.sse"] = sse_module

    try:
        spec = spec_from_file_location("test_incident_memory_phase7_api_module", API_PATH)
        assert spec and spec.loader
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in backups.items():
            if original is not None:
                sys.modules[name] = original
            else:
                sys.modules.pop(name, None)


def _disk_state(**overrides):
    state = {
        "session_id": "incident-session-001",
        "input": "请检查服务器当前磁盘空间使用情况，并分析主要占用来源。",
        "selected_profile": {"profile_id": "disk_pressure_profile"},
        "target_alert": {"alert_name": "HighDiskUsage", "severity": "critical", "host": "demo-server-01"},
        "evidence_store": {
            "disk_usage": {
                "status": "collected",
                "source": "mock",
                "attempts": 1,
                "payload": {"host": "demo-server-01", "usage_percent": 92.4, "used_gb": 184.8, "available_gb": 15.2},
            },
            "large_files": {
                "status": "collected",
                "source": "mock",
                "attempts": 1,
                "payload": {"files": [{"path": "/var/log/app.log", "size_gb": 18.6}]},
            },
        },
        "tools_used": ["get_disk_usage", "list_large_files", "retrieve_knowledge"],
        "response": "# AIOps 磁盘专项诊断报告\n\n## 巡检结论\n- demo-server-01 出现磁盘压力。\n\n## 处理建议\n- 优先检查大文件与 Docker build cache。",
        "verifier_result": {"passed": True},
        "remediation_candidates": [{"action_id": "docker_builder_prune", "title": "清理 Docker build cache", "risk_level": "approval_required"}],
        "feedback": {"helpful": True, "comment": "token=abc123 should be redacted"},
    }
    state.update(overrides)
    return state


def test_append_and_search_similar_incidents(tmp_path, monkeypatch):
    incident_memory, _ = _load_incident_memory_module(enabled=True, debug_api=True)
    monkeypatch.setattr(incident_memory, "INCIDENT_DIR", tmp_path)
    monkeypatch.setattr(incident_memory, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")

    record = incident_memory.build_incident_record(_disk_state())
    incident_memory.append_incident(record)

    lines = incident_memory.INCIDENTS_PATH.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["profile_id"] == "disk_pressure_profile"
    assert payload["final_report_summary"]

    results = incident_memory.search_similar_incidents(
        "磁盘空间又高了怎么办",
        profile_id="disk_pressure_profile",
        host="demo-server-01",
        top_k=3,
    )
    assert results
    assert results[0]["incident_id"] == record["incident_id"]
    assert any("same profile_id" in reason or "same host" in reason for reason in results[0]["matched_reasons"])


def test_failed_incident_keeps_error_summary_and_bad_lines_are_skipped(tmp_path, monkeypatch):
    incident_memory, _ = _load_incident_memory_module(enabled=True, debug_api=True)
    monkeypatch.setattr(incident_memory, "INCIDENT_DIR", tmp_path)
    monkeypatch.setattr(incident_memory, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")

    incident_memory.INCIDENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    incident_memory.INCIDENTS_PATH.write_text("{bad json}\n", encoding="utf-8")

    failed = incident_memory.build_incident_record(
        _disk_state(response="tool execution failed", error_summary="tool execution failed"),
        status="failed",
    )
    incident_memory.append_incident(failed)

    results = incident_memory.search_similar_incidents("磁盘空间异常", profile_id="disk_pressure_profile")
    assert results
    assert results[0]["status"] == "failed"
    assert failed["error_summary"] == "tool execution failed"


def test_incident_debug_api_list_and_search(tmp_path, monkeypatch):
    incident_memory, config_obj = _load_incident_memory_module(enabled=True, debug_api=True)
    monkeypatch.setattr(incident_memory, "INCIDENT_DIR", tmp_path)
    monkeypatch.setattr(incident_memory, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")

    incident_memory.append_incident(incident_memory.build_incident_record(_disk_state()))
    api_module = _load_aiops_api_module(incident_memory, config_obj)

    list_result = asyncio.run(api_module.list_incidents_debug(limit=20))
    assert list_result["code"] == 200
    assert list_result["data"]
    assert list_result["data"][0]["profile_id"] == "disk_pressure_profile"

    search_result = asyncio.run(
        api_module.search_incidents_debug(
            query="磁盘空间又高了怎么办",
            profile_id="disk_pressure_profile",
            host="demo-server-01",
            top_k=3,
        )
    )
    assert search_result["code"] == 200
    assert search_result["data"]
    assert search_result["data"][0]["profile_id"] == "disk_pressure_profile"


def test_incident_debug_api_disabled_returns_404(tmp_path, monkeypatch):
    incident_memory, config_obj = _load_incident_memory_module(enabled=True, debug_api=False)
    monkeypatch.setattr(incident_memory, "INCIDENT_DIR", tmp_path)
    monkeypatch.setattr(incident_memory, "INCIDENTS_PATH", tmp_path / "incidents.jsonl")
    api_module = _load_aiops_api_module(incident_memory, config_obj)

    try:
        asyncio.run(api_module.list_incidents_debug(limit=20))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("Expected HTTPException when incident debug API is disabled")
