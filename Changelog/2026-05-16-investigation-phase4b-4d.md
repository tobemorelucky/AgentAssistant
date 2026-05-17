# 2026-05-16 Investigation Phase 4B-4D

## Summary

本轮完成 CPU / Memory 诊断接入统一 Investigation Engine，并让默认巡检可以分发到对应 Profile。

## Changes

- `app/monitoring/monitor_provider.py`
  - 新增 `get_memory_summary_data`
  - 新增 `list_top_memory_processes_data`
  - 新增 `get_cpu_summary_data`
  - 新增 `list_top_cpu_processes_data`
  - 统一复用 remote_host 请求封装、`X-Host-Agent-Token` 鉴权和结构化错误返回
- `mcp_servers/monitor_server.py`
  - 新增 MCP tools：
    - `get_memory_summary`
    - `list_top_memory_processes`
    - `get_cpu_summary`
    - `list_top_cpu_processes`
- `app/agent/aiops/investigation/profiles.py`
  - 新增 `memory_pressure_profile`
  - 新增 `cpu_pressure_profile`
  - 保持 `patrol_dispatch_profile` / `disk_pressure_profile`
- `app/agent/aiops/investigation/memory_engine.py`
  - 新增 Memory Runtime 证据逻辑、报告逻辑和 verifier 逻辑
- `app/agent/aiops/investigation/cpu_engine.py`
  - 新增 CPU Runtime 证据逻辑、报告逻辑和 verifier 逻辑
- `app/agent/aiops/investigation/runtime.py`
  - runtime registry 注册 `MemoryInvestigationRuntime`
  - runtime registry 注册 `CpuInvestigationRuntime`
- `app/agent/aiops/investigation/patrol_dispatch.py`
  - 默认巡检支持：
    - `HighDiskUsage` / `DiskFull` -> `disk_pressure_profile`
    - `HighCPUUsage` -> `cpu_pressure_profile`
    - `HighMemoryUsage` / `MemoryPressure` -> `memory_pressure_profile`
- `app/agent/aiops/skill_router.py`
  - 修复 CPU / Memory / Disk 触发词与意图识别
- `skills/memory_pressure/SKILL.md`
  - 新增 memory execution profile skill
- `skills/cpu_pressure/SKILL.md`
  - 新增 cpu execution profile skill
- `skills/disk_cleanup/SKILL.md`
  - 重写为 clean UTF-8 的 execution profile skill
- `mock_data/disk.json`
  - 新增 memory / cpu mock summary 和 top process 数据
- `docs/aiops_investigation_architecture.md`
  - 更新到 Phase 4 当前状态

## Validation

- `python -m py_compile ...` 通过
- `python -m pytest tests\test_monitor_provider.py tests\agent\test_investigation_phase1.py tests\agent\test_investigation_phase3.py tests\agent\test_skill_router.py tests\agent\test_memory_cpu_investigation_phase4.py tests\agent\test_disk_investigation_phase2.py -o addopts=''`
  - `37 passed`

## Notes

- 第一版 Memory / CPU Runtime 只接入状态摘要、热点进程和 Runbook 参考；
- 尚未强行引入日志、历史工单或技术栈特定证据；
- 这样可以先保证流程受控、报告收敛、不会回退到旧 generic 长链。
