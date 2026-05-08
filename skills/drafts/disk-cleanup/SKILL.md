---
name: Draft disk_cleanup
description: Auto-generated draft from session session-1778227492392-q5zgrv.
tools:
  - retrieve_knowledge
  - list_all_services
  - retrieve_knowledge
  - list_all_services
  - get_active_alerts
  - list_all_services
risk_level: low_risk
trigger:
  keywords:
    - 服务器磁盘使用率过高，怀疑硬盘满了，请给出清理建议
  intents:
    - incident_followup
steps:
  - 立即在目标服务器上执行 `df -h` 和 `du -h --max-depth=1 / | sort -rh | head -n 10`，获取实际的磁盘使用数据。
  - 根据 `du` 命令的输出结果，深入占用空间最大的目录，使用 `find` 命令定位具体的大文件。
  - 检查占用空间最大的文件是否被进程占用（使用 `lsof | grep deleted`），避免无效清理。
  - 基于实际排查到的文件类型和路径，重新生成针对性的清理报告，仅包含与实际情况匹配的清理建议。
  - 在执行任何删除操作前，务必对相关数据进行备份或快照。
output_format:
  - Root cause
  - Evidence
  - Risk
  - Recommendation
---

# Draft disk_cleanup

This draft was generated from a completed diagnosis session. Review before enabling.