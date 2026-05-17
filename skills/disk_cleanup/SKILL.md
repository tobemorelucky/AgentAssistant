---
name: disk_cleanup
description: 针对磁盘使用率过高、磁盘空间不足和清理建议场景的结构化诊断入口。
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
    - 清理缓存
  intents:
    - disk_diagnosis
steps:
  - 采集磁盘总览、主要目录、大文件与 Docker 占用证据。
  - 基于实时证据判断主要容量来源和证据边界。
  - 补充 deleted open files 与 Runbook 参考。
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

这个 Skill 只负责把请求路由到 `disk_pressure_profile`，由统一 Investigation Engine 生成结构化任务、采集证据并输出报告。
