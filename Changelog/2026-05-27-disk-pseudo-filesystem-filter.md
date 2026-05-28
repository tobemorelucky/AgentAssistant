# 2026-05-27 磁盘伪文件系统过滤修复

## Summary
- 修复远程 Host Agent 磁盘目录/文件结果中 `/proc`、`/sys`、`/dev`、`/run`、`/var/run`、`/tmp/.mount*` 被误当作真实磁盘占用的问题。

## Changes
- `app/monitoring/monitor_provider.py`
  - 新增伪文件系统路径识别与过滤逻辑。
  - `list_large_directories_data()` 和 `list_large_files_data()` 在 `mock` / `remote_host` 两种模式下都会过滤伪文件系统路径。
- `app/agent/aiops/disk_cleanup.py`
  - 新增磁盘目录/文件证据过滤 helper。
  - `normalize_disk_tool_result()`、`build_disk_cleanup_report()`、`build_disk_verifier_findings()` 统一过滤伪文件系统路径，避免旧数据或异常返回进入报告。
- `app/agent/aiops/investigation/disk_engine.py`
  - Top 目录 / Top 大文件展示与说明逻辑改为使用过滤后的结果。

## Verification
- `python -m py_compile app\\monitoring\\monitor_provider.py app\\agent\\aiops\\disk_cleanup.py app\\agent\\aiops\\investigation\\disk_engine.py tests\\test_monitor_provider.py tests\\agent\\test_disk_cleanup_report.py`
- `python -m pytest tests\\test_monitor_provider.py tests\\agent\\test_disk_cleanup_report.py -o addopts=''`
- 结果：`22 passed`
