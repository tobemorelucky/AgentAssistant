# 2026-05-18 CPU / Memory Evidence Failure Fix

## Summary
- 修复 CPU / Memory Profile 在工具调用失败时仍被错误视为“部分证据已满足”的问题。
- 收紧 required evidence 的 usable 判定，避免 `unknown-host` / 空列表 / 错误 payload 被提前收口。

## Changes
- 重写 `app/agent/aiops/investigation/cpu_engine.py`
  - 将 `error` / `error_code` / 空 payload 统一识别为失败结果。
  - `cpu_summary` 仅在返回可用摘要字段时才算满足 required evidence。
  - `top_cpu_processes` 仅在进程列表非空时才算满足 required evidence。
  - 报告缺口明确写出 CPU 摘要或热点进程列表是否未成功获取。
- 重写 `app/agent/aiops/investigation/memory_engine.py`
  - 将 `error` / `error_code` / 空 payload 统一识别为失败结果。
  - `memory_summary` 仅在返回可用摘要字段时才算满足 required evidence。
  - `top_memory_processes` 仅在进程列表非空时才算满足 required evidence。
  - 报告缺口明确写出内存摘要或热点进程列表是否未成功获取。
- 更新 `tests/agent/test_memory_cpu_investigation_phase4.py`
  - 覆盖 CPU / Memory 工具错误 payload。
  - 覆盖 required evidence 缺失时的报告缺口文案。
  - 保留有效结果可正常 finalize 的回归验证。

## Verification
- `python -m py_compile app\\agent\\aiops\\investigation\\cpu_engine.py app\\agent\\aiops\\investigation\\memory_engine.py tests\\agent\\test_memory_cpu_investigation_phase4.py`
- `python -m pytest tests\\agent\\test_memory_cpu_investigation_phase4.py -o addopts=''`
- `python -m pytest tests\\agent\\test_investigation_phase3.py tests\\agent\\test_disk_investigation_phase2.py -o addopts=''`
