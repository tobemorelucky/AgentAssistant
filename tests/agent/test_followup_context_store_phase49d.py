import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FOLLOWUP_CONTEXT_PATH = ROOT / "app" / "agent" / "aiops" / "followup_context.py"
RUNTIME_STORE_PATH = ROOT / "app" / "agent" / "aiops" / "runtime_store.py"


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


followup_context = _load_module("app.agent.aiops.followup_context", FOLLOWUP_CONTEXT_PATH)
runtime_store_module = _load_module("app.agent.aiops.runtime_store", RUNTIME_STORE_PATH)


def test_previous_aiops_context_can_be_saved_and_restored_by_session_id():
    session_id = "followup-store-test-session"
    context = {
        "previous_user_query": "CPU满了怎么办",
        "previous_profile_id": "cpu_pressure_profile",
        "previous_target_object": "he-VMware-Virtual-Platform",
        "previous_target_alert": {"alert_name": "HostHighCPUUsage"},
        "previous_diagnosis_summary": "CPU 使用率较高，热点进程为 uvicorn。",
        "previous_key_evidence": ["cpu_summary: usage=91.3%"],
        "previous_recommendations": "先观察热点进程，再评估是否需要限流。",
        "previous_runbook_summary": "CPU 高使用率排查 runbook 已引用。",
        "previous_external_search_used": False,
        "previous_action_safety_notes": "未执行任何重启或 kill -9。",
    }
    runtime_store_module.runtime_store.save_previous_aiops_context(session_id, context)
    restored = runtime_store_module.runtime_store.load_previous_aiops_context(session_id)
    assert restored == context
    runtime_store_module.runtime_store.clear_session(session_id)


def test_failure_followup_with_previous_context_is_dependent():
    previous_context = {
        "previous_user_query": "CPU满了怎么办",
        "previous_profile_id": "cpu_pressure_profile",
    }
    relation = followup_context.classify_followup_relation("按你说的重新运行了没有效果", previous_context)
    assert relation["relation_type"] == "dependent_followup"
    assert relation["recommended_handling"] == "followup_decision"
