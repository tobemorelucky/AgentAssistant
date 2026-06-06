import asyncio
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
API_PATH = ROOT / "app" / "api" / "aiops.py"
SESSION_MEMORY_PATH = ROOT / "app" / "agent" / "aiops" / "memory" / "session_memory.py"


def _load_session_memory_module(enabled: bool = True, debug_api: bool = False):
    original_app = sys.modules.get("app")
    original_app_config = sys.modules.get("app.config")

    fake_app = types.ModuleType("app")
    fake_app.__path__ = []  # type: ignore[attr-defined]
    fake_config = types.ModuleType("app.config")
    fake_config.config = types.SimpleNamespace(
        aiops_session_memory_enabled=enabled,
        aiops_session_memory_debug_api=debug_api,
        debug=False,
        aiops_session_memory_backend="file",
        aiops_session_memory_window=20,
        aiops_session_memory_summarize_batch=15,
        aiops_session_memory_max_turn_chars=4000,
        rag_model="qwen-max",
    )

    sys.modules["app"] = fake_app
    sys.modules["app.config"] = fake_config

    try:
        spec = spec_from_file_location("test_session_memory_debug_api_module_memory", SESSION_MEMORY_PATH)
        assert spec and spec.loader
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, fake_config.config
    finally:
        if original_app is not None:
            sys.modules["app"] = original_app
        else:
            sys.modules.pop("app", None)
        if original_app_config is not None:
            sys.modules["app.config"] = original_app_config
        else:
            sys.modules.pop("app.config", None)


def _load_aiops_api_module(session_memory_module, config_obj):
    backups = {name: sys.modules.get(name) for name in [
        "app",
        "app.api",
        "app.agent",
        "app.agent.aiops",
        "app.agent.aiops.heartbeat",
        "app.agent.aiops.memory",
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
    ]}

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
    sys.modules["app.agent.aiops.memory.session_memory"] = session_memory_module

    heartbeat_module = types.ModuleType("app.agent.aiops.heartbeat")
    heartbeat_module.heartbeat_manager = types.SimpleNamespace(latest=lambda: {}, history=lambda limit=20: [], run_once=lambda **kwargs: {})
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
        spec = spec_from_file_location("test_session_memory_debug_api_module", API_PATH)
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


def _turn(index: int):
    return {
        "turn_id": f"turn-{index}",
        "created_at": "2026-06-06T00:00:00+00:00",
        "status": "completed",
        "user_input": f"question {index}",
        "mode": "custom",
        "selected_profile": "cpu_pressure_profile",
        "plan_source": "investigation_runtime",
        "tools_used": ["get_cpu_summary"],
        "evidence_summary": {},
        "verifier_passed": True,
        "risk_events": [],
        "final_report_summary": f"turn {index} summary",
        "error_summary": "",
        "remediation_candidates": [],
    }


def test_session_memory_debug_api_get_and_delete(tmp_path, monkeypatch):
    session_memory, config_obj = _load_session_memory_module(enabled=True, debug_api=True)
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)
    asyncio.run(session_memory.append_session_turn("mem-dev-001", _turn(1)))
    api_module = _load_aiops_api_module(session_memory, config_obj)

    get_result = asyncio.run(api_module.get_session_memory_debug("mem-dev-001"))
    assert get_result["code"] == 200
    assert get_result["data"]["session_id"] == "mem-dev-001"
    assert len(get_result["data"]["recent_turns"]) == 1

    delete_result = asyncio.run(api_module.delete_session_memory_debug("mem-dev-001"))
    assert delete_result["code"] == 200

    get_after_delete = asyncio.run(api_module.get_session_memory_debug("mem-dev-001"))
    assert get_after_delete["data"]["recent_turns"] == []
    assert get_after_delete["data"]["turn_count"] == 0


def test_session_memory_debug_api_disabled_returns_404(tmp_path, monkeypatch):
    session_memory, config_obj = _load_session_memory_module(enabled=True, debug_api=False)
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)
    api_module = _load_aiops_api_module(session_memory, config_obj)

    try:
        asyncio.run(api_module.get_session_memory_debug("mem-dev-001"))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 404
    else:
        raise AssertionError("Expected HTTPException when debug API is disabled")
