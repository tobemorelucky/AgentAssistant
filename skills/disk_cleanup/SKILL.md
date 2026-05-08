---
name: disk_cleanup
description: 基于纯模拟磁盘现场数据的磁盘容量诊断与清理建议 Skill，用于在磁盘使用率过高、空间不足或怀疑日志/缓存膨胀时，生成有证据的 AIOps 报告。
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
  - 先确认根挂载点或目标挂载点的磁盘使用率、剩余空间和主机信息。
  - 再定位高占用目录和大文件，明确主要的空间消耗来源。
  - 检查 deleted open files，确认是否存在“文件已删但空间未释放”的情况。
  - 补充 Docker 镜像、卷、构建缓存占用，避免漏掉容器运行时目录。
  - 输出可安全清理项、需人工确认项和禁止自动清理项，并附带风险提示。
output_format:
  - 磁盘使用率证据
  - Top 目录占用
  - Top 大文件
  - Docker 占用情况
  - deleted open files
  - 可安全清理项
  - 需人工确认项
  - 禁止自动清理项
  - 风险提示
  - 后续预防措施
---

# Disk Cleanup Runbook

## 适用场景

- 磁盘使用率持续升高，接近或超过 90%
- 业务报错出现 `No space left on device`
- 怀疑日志、Docker 缓存、临时目录或删除未释放文件占满空间

## 诊断原则

- 优先采集证据，不凭经验直接下清理结论
- 清理建议必须区分“可安全清理”“需人工确认”“禁止自动清理”
- 所有删除动作仅作为建议展示，不能声称已经执行

## 风险边界

- `rm -rf` 只能作为高风险建议，不能自动执行
- `docker system prune --volumes` 必须标记为高风险
- 数据库目录、业务 `uploads/`、Milvus/MinIO/etcd volumes 属于禁止自动清理项
