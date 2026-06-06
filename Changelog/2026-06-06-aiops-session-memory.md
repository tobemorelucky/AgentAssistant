# 2026-06-06 AIOps Session Memory

## Summary
- Added file-backed AIOps session memory for repeated requests under the same `session_id`.
- Each request can load a long-term summary plus recent turns.
- After enough turns accumulate, older turns are summarized and compacted.

## Hardening
- Respect `AIOPS_SESSION_MEMORY_ENABLED`
  - disabled mode returns empty session context
  - disabled mode skips file writes
  - disabled mode skips LLM summarization
  - `aiops_service` still runs and emits a `Session memory disabled` trace
- Safer storage
  - session filenames now use sanitized ids only
  - writes are atomic via `.tmp` plus replace
  - broken JSON files are renamed to `.broken` and ignored
- Prompt and payload limits
  - `user_input` and `final_report_summary` are trimmed before summary prompts
  - `risk_events` and `tools_used` are capped
  - summary prompts are deterministically truncated before 12000 chars
- Failure turns
  - failed turns now persist `error_summary` together with the original task

## Files
- `app/agent/aiops/memory/session_memory.py`
- `app/services/aiops_service.py`
- `app/agent/aiops/planner.py`
- `app/agent/aiops/state.py`
- `app/config.py`
- `.env`
- `.env.example`
- `tests/agent/test_session_memory_phase6.py`
- `scripts/test_aiops_session_memory.py`

## Verification
- `python -m py_compile app\\agent\\aiops\\memory\\session_memory.py app\\services\\aiops_service.py tests\\agent\\test_session_memory_phase6.py scripts\\test_aiops_session_memory.py`
- `python -m pytest tests\\agent\\test_session_memory_phase6.py -o addopts=''`
- `python scripts\\test_aiops_session_memory.py`
- Result: all checks passed in this round.
