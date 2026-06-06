import asyncio
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "app" / "agent" / "aiops" / "memory" / "session_memory.py"


def _load_module(enabled: bool = True):
    original_app = sys.modules.get("app")
    original_app_config = sys.modules.get("app.config")

    fake_app = types.ModuleType("app")
    fake_app.__path__ = []  # type: ignore[attr-defined]
    fake_config = types.ModuleType("app.config")
    fake_config.config = types.SimpleNamespace(
        aiops_session_memory_enabled=enabled,
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


def _turn(index: int, **overrides):
    payload = {
        "turn_id": f"turn-{index}",
        "created_at": "2026-06-06T00:00:00+00:00",
        "status": "completed",
        "user_input": f"question {index}",
        "mode": "custom",
        "selected_profile": "cpu_pressure_profile",
        "plan_source": "investigation_runtime",
        "tools_used": ["get_cpu_summary", "list_top_cpu_processes"],
        "evidence_summary": {"cpu_summary": {"status": "collected", "usage_percent": 88.7}},
        "verifier_passed": True,
        "risk_events": [],
        "final_report_summary": f"turn {index} summary",
        "error_summary": "",
        "remediation_candidates": [],
    }
    payload.update(overrides)
    return payload


def test_append_and_build_session_context_keeps_recent_turns(tmp_path, monkeypatch):
    session_memory = _load_module(enabled=True)
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)

    session_id = "session-memory-basic"
    session_memory.save_session_memory(session_id, {"session_id": session_id, "recent_turns": [], "turn_count": 0})
    for index in range(1, 4):
        asyncio.run(session_memory.append_session_turn(session_id, _turn(index)))

    context = session_memory.build_session_context(session_id)
    assert context["turn_count"] == 3
    assert len(context["recent_turns"]) == 3
    assert context["recent_turns"][-1]["user_input"] == "question 3"


def test_session_memory_rolls_up_after_21_turns(tmp_path, monkeypatch):
    session_memory = _load_module(enabled=True)
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)

    session_id = "session-memory-rollup"

    async def fake_summary(existing_summary, old_turns):
        return f"summary of {len(old_turns)} turns"

    monkeypatch.setattr(session_memory, "_summarize_turn_batch_with_llm", fake_summary)
    session_memory.save_session_memory(session_id, {"session_id": session_id, "recent_turns": [], "turn_count": 0})

    for index in range(1, 22):
        asyncio.run(session_memory.append_session_turn(session_id, _turn(index)))

    payload = session_memory.load_session_memory(session_id)
    assert payload["turn_count"] == 21
    assert payload["long_term_summary"] == "summary of 15 turns"
    assert len(payload["recent_turns"]) <= 6
    assert payload["recent_turns"][0]["user_input"] == "question 16"


def test_session_id_is_sanitized_for_filename_and_cannot_escape_directory(tmp_path, monkeypatch):
    session_memory = _load_module(enabled=True)
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)

    session_id = "../unsafe/session:id"
    asyncio.run(session_memory.append_session_turn(session_id, _turn(1)))

    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert files[0].parent == tmp_path
    assert ".." not in files[0].name
    assert "/" not in files[0].name
    assert ":" not in files[0].name


def test_sensitive_values_are_redacted_in_saved_turn(tmp_path, monkeypatch):
    session_memory = _load_module(enabled=True)
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)

    session_id = "session-redaction"
    turn = _turn(
        1,
        user_input="token=abc123 password=secret api_key=xyz",
        final_report_summary="Bearer token123",
    )
    asyncio.run(session_memory.append_session_turn(session_id, turn))

    payload = session_memory.load_session_memory(session_id)
    saved_turn = payload["recent_turns"][0]
    assert "[REDACTED]" in saved_turn["user_input"]
    assert "abc123" not in saved_turn["user_input"]


def test_enabled_false_returns_empty_context_and_does_not_write(tmp_path, monkeypatch):
    session_memory = _load_module(enabled=False)
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)

    session_id = "disabled-session"
    context = session_memory.build_session_context(session_id)
    updated = asyncio.run(session_memory.append_session_turn(session_id, _turn(1)))
    summarized = asyncio.run(session_memory.maybe_summarize_session_memory(session_id))

    assert context["turn_count"] == 0
    assert updated["turn_count"] == 0
    assert summarized["long_term_summary"] == ""
    assert not list(tmp_path.iterdir())


def test_broken_json_is_renamed_and_default_memory_returned(tmp_path, monkeypatch):
    session_memory = _load_module(enabled=True)
    monkeypatch.setattr(session_memory, "SESSION_MEMORY_DIR", tmp_path)

    path = session_memory._session_memory_path("broken-session")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{invalid json", encoding="utf-8")

    payload = session_memory.load_session_memory("broken-session")
    assert payload["recent_turns"] == []
    assert path.with_suffix(path.suffix + ".broken").exists()


def test_failed_turn_summary_contains_error_summary():
    session_memory = _load_module(enabled=True)
    summary = session_memory.build_turn_summary(
        {
            "input": "cpu is still high",
            "mode": "custom",
            "selected_profile": {"profile_id": "cpu_pressure_profile"},
            "plan_source": "investigation_runtime",
            "response": "tool execution failed",
            "error_summary": "tool execution failed",
            "tools_used": ["get_cpu_summary"],
            "trace_events": [],
            "verifier_result": {},
            "remediation_candidates": [],
        },
        status="failed",
    )
    assert summary["status"] == "failed"
    assert summary["user_input"] == "cpu is still high"
    assert summary["error_summary"] == "tool execution failed"
