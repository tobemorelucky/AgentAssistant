---
name: disk_cleanup
description: 磁盘使用率过高时的本地排查与清理 Runbook，用于指导 Agent 分析磁盘空间占用、定位大文件、清理缓存与日志，并输出安全的处理建议。
tools:
  - retrieve_knowledge
  - query_process_list
  - search_service_logs
  - search_historical_tickets
risk_level: low_risk
trigger:
  alerts:
    - HighDiskUsage
    - DiskUsageHigh
    - DiskFull
  keywords:
    - 磁盘
    - 硬盘
    - 磁盘满
    - 硬盘满
    - disk
    - disk usage
    - no space left
    - storage
    - 清理空间
    - 清理缓存
  intents:
    - log_analysis
steps:
  - 确认告警对象、磁盘挂载点、使用率、剩余空间和告警持续时间。
  - 优先判断是否为日志暴涨、缓存堆积、构建产物堆积、临时文件堆积、Docker 数据膨胀或依赖缓存膨胀。
  - 检查最近是否有服务异常、日志刷屏、任务重试、备份任务或批处理任务导致文件快速增长。
  - 在不执行危险删除操作的前提下，列出安全清理建议和需要人工确认的高风险操作。
  - 输出清理优先级：可安全清理、需确认后清理、禁止自动清理。
output_format:
  - 磁盘告警摘要
  - 可疑占用来源
  - 推荐排查命令
  - 安全清理建议
  - 高风险操作提醒
  - 后续预防措施
---

# 磁盘清理 Runbook

## 适用场景

当系统出现以下现象时，使用本 Skill：

- 磁盘使用率超过 80% / 90%
- 出现 `No space left on device`
- 日志无法写入
- Docker 构建或数据库写入失败
- 应用因磁盘空间不足异常退出

## 诊断思路

### 1. 先确认告警范围

需要确认：

- 是哪个服务触发告警
- 是哪个主机或容器实例
- 是哪个挂载点，例如 `/`、`/var`、`/data`
- 当前使用率、剩余空间、增长速度
- 告警是否仍在持续

### 2. 常见原因

#### 原因一：日志文件过大

典型现象：

- `/var/log` 或应用日志目录快速增长
- ERROR / WARN 日志数量异常
- 某个服务反复重试或报错

建议：

- 查看最近增长最快的日志文件
- 检查是否有日志刷屏
- 优先做日志轮转、压缩或归档
- 不要直接删除正在被进程占用的日志文件

#### 原因二：Docker 数据膨胀

典型现象：

- Docker 镜像、容器、volume、build cache 占用过高
- `/var/lib/docker` 目录过大
- 多次构建后空间持续减少

建议：

- 清理无用容器
- 清理 dangling images
- 清理 build cache
- 删除 volume 前必须确认没有业务数据

高风险提醒：

- `docker system prune -a --volumes` 可能删除未使用镜像和 volume，存在数据丢失风险。
- 生产环境不得直接自动执行，需要人工审批。

#### 原因三：依赖缓存堆积

常见目录：

- Maven: `~/.m2/repository`
- npm: `~/.npm`
- pnpm: `~/.pnpm-store`
- pip: `~/.cache/pip`
- conda: `pkgs`
- HuggingFace: `~/.cache/huggingface`

建议：

- 本地开发环境可以清理缓存
- 生产环境清理前确认不会影响部署和回滚

#### 原因四：临时文件堆积

常见目录：

- `/tmp`
- `C:\Users\<user>\AppData\Local\Temp`
- 项目目录下的 `tmp/`
- 构建产物目录，例如 `dist/`、`build/`、`target/`

建议：

- 优先删除明确可再生成的临时文件
- 不要删除未知业务目录
- 删除前先统计大小和最近修改时间

#### 原因五：数据库或对象存储数据增长

典型目录：

- MySQL / PostgreSQL 数据目录
- Milvus / MinIO / etcd volumes
- Elasticsearch / Loki / Prometheus 数据目录

高风险提醒：

- 这类目录不能直接删除。
- 只能通过业务系统的清理、归档、压缩、TTL 策略处理。

## 推荐排查命令

### Linux

```bash
df -h
du -h --max-depth=1 / 2>/dev/null | sort -h
du -h --max-depth=1 /var 2>/dev/null | sort -h
find /var/log -type f -size +100M -exec ls -lh {} \;