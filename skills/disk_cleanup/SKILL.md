---
name: disk_cleanup
description: 基于磁盘使用率、目录占用、大文件、Docker 占用和 deleted open files 的结构化磁盘诊断 Profile。
skill_mode: execution_profile
profile_id: disk_pressure_profile
tools:
  - get_disk_usage
  - list_large_directories
  - list_large_files
  - query_deleted_open_files
  - query_docker_disk_usage
  - get_disk_cleanup_candidates
  - retrieve_knowledge
risk_level: low_risk
trigger:
  alerts:
    - HighDiskUsage
    - DiskFull
  keywords:
    - 磁盘
    - 硬盘
    - disk
    - disk full
    - no space left
    - 清理空间
  intents:
    - disk_diagnosis
steps:
  - 先确认真实磁盘使用率和主机信息。
  - 再收集 Top 目录和 Top 大文件，定位主要容量来源。
  - 必要时补充 Docker 占用、deleted open files 和本地 Runbook 参考。
output_format:
  - 任务与对象
  - 已确认事实
  - 主要容量来源
  - 候选风险 / 待验证解释
  - 证据缺口
  - 处理建议
  - 风险提示
  - Runbook 参考
---

# Disk Pressure Profile

这个 Skill 现在只负责把“磁盘压力”场景路由到 `disk_pressure_profile`。
真正的执行计划、证据收集、补查和收口都由新的 Investigation Engine 负责，
而不是直接用 Skill 里的文本步骤驱动执行。
