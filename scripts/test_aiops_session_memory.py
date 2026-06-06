"""Lightweight smoke test for AIOps session memory."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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
        spec = spec_from_file_location("script_session_memory_module", MODULE_PATH)
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


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main() -> None:
    original_dir = session_memory.SESSION_MEMORY_DIR
    original_enabled = session_memory.config.aiops_session_memory_enabled

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            session_memory.SESSION_MEMORY_DIR = temp_path
            session_memory.config.aiops_session_memory_enabled = True

            for index in range(1, 4):
                await session_memory.append_session_turn("basic-session", _turn(index))
            basic_context = session_memory.build_session_context("basic-session")
            _assert(basic_context["turn_count"] == 3, "recent_turns should keep 3 turns")

            async def fake_summary(existing_summary, old_turns):
                return f"summary of {len(old_turns)} turns"

            original_summary = session_memory._summarize_turn_batch_with_llm
            session_memory._summarize_turn_batch_with_llm = fake_summary
            try:
                for index in range(1, 22):
                    await session_memory.append_session_turn("rollup-session", _turn(index))
                rollup_payload = session_memory.load_session_memory("rollup-session")
                _assert(bool(rollup_payload["long_term_summary"]), "long_term_summary should not be empty")
                _assert(len(rollup_payload["recent_turns"]) <= 6, "recent_turns should be compacted to <= 6")
            finally:
                session_memory._summarize_turn_batch_with_llm = original_summary

            unsafe_session_id = "../unsafe/session:id"
            await session_memory.append_session_turn(unsafe_session_id, _turn(1))
            unsafe_file = next(temp_path.glob("*.json"))
            _assert(temp_path in unsafe_file.parents, "session file must stay inside aiops_session_memory directory")

            await session_memory.append_session_turn(
                "redaction-session",
                _turn(1, user_input="token=abc123 password=secret api_key=xyz"),
            )
            redaction_payload = session_memory.load_session_memory("redaction-session")
            redaction_text = redaction_payload["recent_turns"][0]["user_input"]
            _assert("[REDACTED]" in redaction_text, "sensitive values must be redacted")
            _assert("abc123" not in redaction_text, "raw token should not be stored")

            session_memory.config.aiops_session_memory_enabled = False
            await session_memory.append_session_turn("disabled-session", _turn(1))
            disabled_path = session_memory._session_memory_path("disabled-session")
            _assert(not disabled_path.exists(), "disabled mode should not write files")
    finally:
        session_memory.SESSION_MEMORY_DIR = original_dir
        session_memory.config.aiops_session_memory_enabled = original_enabled
    print("AIOps session memory smoke test passed.")


if __name__ == "__main__":
    asyncio.run(main())
