# 2026-06-06 AIOps Session Memory

## Summary
- 为 AIOps 增加 file backend 的 Session Memory。
- 同一 `session_id` 下，每次请求默认加载历史摘要与最近轮次。
- 超过 20 轮后会自动滚动摘要最旧 15 轮，保留最近 6 轮。
- 总结失败时走确定性 fallback，不阻塞主流程。

## Changes
- `app/agent/aiops/memory/session_memory.py`
  - 新增 `load_session_memory`
  - 新增 `save_session_memory`
  - 新增 `build_session_context`
  - 新增 `append_session_turn`
  - 新增 `maybe_summarize_session_memory`
- `app/services/aiops_service.py`
  - 初始 state 默认加载 session memory
  - 追加 `Session memory loaded` trace
  - complete / failed 时写入 turn summary
- `app/agent/aiops/planner.py`
  - follow-up resolver / answer prompt 注入 session memory context
- `app/config.py` / `.env` / `.env.example`
  - 新增 Session Memory 配置

## Verification
- `python -m py_compile app\\config.py app\\agent\\aiops\\state.py app\\agent\\aiops\\memory\\session_memory.py app\\services\\aiops_service.py app\\agent\\aiops\\planner.py tests\\agent\\test_session_memory_phase6.py`
- `python -m pytest tests\\agent\\test_session_memory_phase6.py tests\\agent\\test_followup_context_store_phase49d.py tests\\agent\\test_rag_guard_and_followup.py -o addopts=''`
- 结果：`14 passed`
