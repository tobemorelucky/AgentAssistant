# 2026-05-18 Tool Policy Phase 4 Tools

## Summary
- 修复 Phase 4 新增 CPU / Memory / Patrol MCP Tool 未加入 `tool_policy.yaml`，导致网页 AIOps 诊断链路被默认 `blocked` 的问题。

## Changes
- 在 `tool_policy.yaml` 中新增并放行为 `read_only`：
  - `get_patrol_alerts`
  - `get_cpu_summary`
  - `list_top_cpu_processes`
  - `get_memory_summary`
  - `list_top_memory_processes`
- 在 `tests/agent/test_tool_policy.py` 中补充最小回归测试，确保以上工具不会再返回 `blocked`。
- 在 `README.md` 中补充说明：新增 AIOps MCP Tool 时必须同步更新 `tool_policy.yaml`。

## Why
- 调试脚本可直接调用 MCP Tool，不会经过 AIOps Executor 的 `check_tool_policy`。
- 网页中的 Agent 诊断链路一定会经过 `check_tool_policy`。
- 因为未知工具默认 `blocked`，所以出现了“debug 直调成功，但网页 Agent 失败”的现象。
