import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "app" / "agent" / "aiops" / "memory" / "session_memory.py"


def _load_module():
    original_app = sys.modules.get("app")
    original_app_config = sys.modules.get("app.config")

    fake_app = types.ModuleType("app")
    fake_app.__path__ = []  # type: ignore[attr-defined]
    fake_config = types.ModuleType("app.config")
    fake_config.config = types.SimpleNamespace(
        aiops_session_memory_enabled=True,
        aiops_session_memory_backend="file",
        aiops_session_memory_window=20,
        aiops_session_memory_summarize_batch=15,
        aiops_session_memory_max_turn_chars=4000,
        rag_model="qwen-max",
    )

    sys.modules["app"] = fake_app
    sys.modules["app.config"] = fake_config

    try:
        spec = spec_from_file_location("test_session_memory_module", MODULE_PATH)
        assert spec and spec.loader
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if original_app is not None:
            sys.modules["app"] = original_app
        else:
            sys.modules.pop("app", None)
        if original_app_config is not None:
            sys.modules["app.config"] = original_app_config
        else:
            sys.modules.pop("app.config", None)


session_memory = _load_module()


def _turn(index: int) -> dict:
    return {
        "turn_id": f"turn-{index}",
        "created_at": "2026-06-06T00:00:00+00:00",
        "status": "completed",
        "user_input": f"问题 {index}",
        "mode": "custom",
        "selected_profile": "cpu_pressure_profile",
        "plan_source": "investigation_runtime",
        "tools_used": ["get_cpu_summary", "list_top_cpu_processes"],
        "evidence_summary": {"cpu_summary": {"status": "collected", "usage_percent": 88.7}},
        "verifier_passed": True,
        "risk_events": [],
        "final_report_summary": f"第 {index} 轮诊断摘要",
        "remediation_candidates": [],
    }


def test_append_and_build_session_context_keeps_recent_turns():
    session_id = "session-memory-basic"
    session_memory.save_session_memory(session_id, {"session_id": session_id, "recent_turns": [], "turn_count": 0})
    for index in range(1, 4):
        import asyncio

        asyncio.run(session_memory.append_session_turn(session_id, _turn(index)))

    context = session_memory.build_session_context(session_id)
    assert context["turn_count"] == 3
    assert len(context["recent_turns"]) == 3
    assert context["recent_turns"][-1]["user_input"] == "问题 3"


def test_session_memory_rolls_up_after_21_turns(monkeypatch):
    session_id = "session-memory-rollup"

    async def fake_summary(existing_summary, old_turns):
        return f"摘要 {len(old_turns)} 轮"

    monkeypatch.setattr(session_memory, "_summarize_turn_batch_with_llm", fake_summary)
    session_memory.save_session_memory(session_id, {"session_id": session_id, "recent_turns": [], "turn_count": 0})

    import asyncio

    for index in range(1, 22):
        asyncio.run(session_memory.append_session_turn(session_id, _turn(index)))

    payload = session_memory.load_session_memory(session_id)
    assert payload["turn_count"] == 21
    assert payload["long_term_summary"] == "摘要 15 轮"
    assert len(payload["recent_turns"]) == 6
    assert payload["recent_turns"][0]["user_input"] == "问题 16"


def test_session_memory_summary_fallback_when_llm_fails(monkeypatch):
    session_id = "session-memory-fallback"

    async def fake_summary(existing_summary, old_turns):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(session_memory, "_summarize_turn_batch_with_llm", fake_summary)
    session_memory.save_session_memory(
        session_id,
        {
            "session_id": session_id,
            "recent_turns": [_turn(index) for index in range(1, 22)],
            "turn_count": 21,
        },
    )

    import asyncio

    payload = asyncio.run(session_memory.maybe_summarize_session_memory(session_id))
    assert payload["long_term_summary"]
    assert "历史结论仅作上下文参考" in payload["long_term_summary"]
    assert len(payload["recent_turns"]) == 6
