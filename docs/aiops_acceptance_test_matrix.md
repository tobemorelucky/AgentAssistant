# AIOps Acceptance Test Matrix

本文档用于固化当前正式 AIOps 架构的验收矩阵。

每个场景均说明：
- 输入
- 期望命中路径
- 期望关键 Trace
- 期望报告要点
- 不应出现的错误现象

## 1. 普通 RAG 实时问题边界
- 输入：
  - `请检查服务器当前磁盘空间使用情况，并分析主要占用来源。`
- 期望命中路径：
  - 普通 RAG -> RAG answer guard
- 期望关键 Trace：
  - 无 AIOps Trace
- 期望报告要点：
  - 明确说明当前为知识问答模式
  - 提示切换到 AIOps 模式进行实时诊断
  - 若引用案例，标记为历史/示例参考
- 不应出现的错误现象：
  - 把 mock/demo 数据写成当前实时事实
  - 直接写出 `demo-server-01 92.4%`

## 2. remote_host 健康默认巡检
- 输入：
  - `请开始一次 AIOps 巡检，并保留完整 Agent Trace。`
- 期望命中路径：
  - `host_health_patrol_profile`
- 期望关键 Trace：
  - `get_cpu_summary`
  - `get_memory_summary`
  - `get_disk_usage`
  - `get_patrol_alerts`
- 期望报告要点：
  - CPU / 内存 / 磁盘实时状态
  - 活跃告警为空或未发现异常
  - 巡检结论为“当前未发现明显资源级异常”
- 不应出现的错误现象：
  - 只执行 `get_patrol_alerts`
  - 没做 CPU / 内存 / 磁盘基础扫描

## 3. remote_host CPU 专项
- 输入：
  - `系统现在 CPU 情况如何？`
- 期望命中路径：
  - `cpu_pressure_profile`
- 期望关键 Trace：
  - `get_cpu_summary`
  - `list_top_cpu_processes`
  - `retrieve_knowledge`
- 期望报告要点：
  - CPU 使用率
  - 热点 CPU 进程
  - 本地 Runbook 参考
- 不应出现的错误现象：
  - `usage=None`
  - `unknown-host`
  - 明明工具失败却写“关键证据已覆盖”

## 4. remote_host Memory 专项
- 输入：
  - `系统现在内存情况如何？`
- 期望命中路径：
  - `memory_pressure_profile`
- 期望关键 Trace：
  - `get_memory_summary`
  - `list_top_memory_processes`
  - `retrieve_knowledge`
- 期望报告要点：
  - 内存使用率
  - 主要内存消耗进程
  - 证据缺口与建议分离
- 不应出现的错误现象：
  - `unknown-host`
  - 摘要工具失败却被当作 `partial` 满足 required evidence

## 5. remote_host Disk 专项
- 输入：
  - `请检查服务器当前磁盘空间使用情况，并分析主要占用来源。`
- 期望命中路径：
  - `disk_pressure_profile`
- 期望关键 Trace：
  - `get_disk_usage`
  - `list_large_directories`
  - `list_large_files`
  - `query_docker_disk_usage`
  - `query_deleted_open_files`
  - `retrieve_knowledge`
- 期望报告要点：
  - 磁盘使用率
  - Top 目录 / Top 文件
  - Docker 占用与 deleted open files
  - 权限边界或证据缺口说明
- 不应出现的错误现象：
  - `unknown% / unknownGB`
  - 把未接入实时的数据写成现场事实

## 6. mock 默认巡检异常自动升级
- 输入：
  - `请开始一次 AIOps 巡检，并保留完整 Agent Trace。`
- 预置：
  - `AIOPS_MONITOR_PROVIDER=mock`
  - `AIOPS_ALERT_PROVIDER=mock`
- 期望命中路径：
  - `host_health_patrol_profile` -> abnormal findings -> auto escalation -> 对应专项 Profile
- 期望关键 Trace：
  - `Abnormal findings detected`
  - `Selected escalation target`
  - `Escalating to ...`
- 期望报告要点：
  - 先给出巡检结论
  - 再说明自动升级到 CPU / Memory / Disk 专项诊断
- 不应出现的错误现象：
  - 只写“建议进入专项诊断”
  - 不真正切到对应 Profile

## 7. 解释型 follow-up 不触发 Tavily
- 第一轮输入：
  - `CPU满了怎么办`
- 第二轮输入：
  - `为什么你建议先观察热点进程？`
- 期望命中路径：
  - `dependent_followup` -> `answer_from_previous_context`
- 期望关键 Trace：
  - `Previous AIOps context loaded`
  - `Follow-up relation classified`
  - `Follow-up branch entered`
- 期望报告要点：
  - 基于上一轮上下文解释
  - 不声称新增实时证据
- 不应出现的错误现象：
  - 直接触发 Tavily
  - 重新跑一轮空的 CPU/Memory/Disk 专项诊断

## 8. 失败反馈型 follow-up 触发 Tavily
- 第一轮输入：
  - `CPU满了怎么办`
- 第二轮输入：
  - `按你说的做了还是没效果`
- 期望命中路径：
  - `dependent_followup` -> follow-up resolver -> `use_tavily_external_search`
- 期望关键 Trace：
  - `Previous AIOps context loaded`
  - `Remediation feedback failed detected`
  - `Follow-up branch entered`
  - `web_search`
- 期望报告要点：
  - 输出“追问补充诊断报告”
  - 关联上一轮诊断摘要
  - 区分本地结论与外部补充参考
  - 给出 2-4 条增量排查方向
- 不应出现的错误现象：
  - `unknown-host`
  - `web_search did not return a structured payload`
  - 重新退回空的 CPU/Memory/Disk 专项报告

## 9. 独立新问题不误用旧上下文
- 第一轮输入：
  - `CPU满了怎么办`
- 第二轮输入：
  - `内存情况怎么样？`
- 期望命中路径：
  - `independent` -> `memory_pressure_profile`
- 期望关键 Trace：
  - `Follow-up relation classified`
  - 不出现 `Follow-up branch entered`
  - `get_memory_summary`
  - `list_top_memory_processes`
- 期望报告要点：
  - 正常进入新的内存专项诊断
  - 不混入上一轮 CPU 结论
- 不应出现的错误现象：
  - 错误复用上一轮 CPU 上下文
  - 把独立新问题当作 dependent follow-up
## 10. Heartbeat healthy summary
- 输入
  - `POST /api/v1/aiops/heartbeat/run`
- 期望命中路径
  - 轻量心跳扫描
  - 不触发深度诊断
- 期望关键 Trace
  - 无专项诊断 Trace
- 期望报告要点
  - 仅保存 heartbeat summary
  - `overall_status=healthy`
- 不应出现
  - 自动 remediation execute
  - Tavily

## 11. Heartbeat abnormal deep diagnosis
- 输入
  - `POST /api/v1/aiops/heartbeat/run`
- 期望命中路径
  - heartbeat summary
  - `host_health_patrol_profile`
  - 自动升级到 CPU / Memory / Disk 专项
- 期望关键 Trace
  - 诊断链与对应专项 Profile 一致
- 期望报告要点
  - 有 diagnosis report summary
  - 有 remediation candidates
- 不应出现
  - 自动 execute

## 12. Remediation dry-run / execute guard
- 输入
  - `POST /api/v1/aiops/remediation/dry-run`
  - `POST /api/v1/aiops/remediation/execute`
- 期望命中路径
  - dry-run 调 Host Agent
  - execute 先过 action policy
- 期望关键 Trace
  - audit/event log 记录 dry-run / execute
- 期望报告要点
  - execute 需要 `approval_token`
  - forbidden action 直接拒绝
- 不应出现
  - 未审批执行成功
  - forbidden action 被放行
