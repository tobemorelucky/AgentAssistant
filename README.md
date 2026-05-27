# AgentAssistant

AgentAssistant 是一个同时支持普通 RAG 知识问答与 AIOps 实时诊断的 Python 项目。

## 模式边界

### 普通 RAG
- 只做知识问答、Runbook 检索、历史案例参考。
- 不读取实时主机状态。
- 如果用户询问当前 CPU / 内存 / 磁盘 / 系统是否异常，会提示切换到 AIOps 模式。

### AIOps
- 面向实时诊断。
- 基于 Host Agent / MCP / 本地工具收集现场证据。
- 结论优先依赖本地实时证据，其次才是本地 Runbook / 外部补充参考。

## 当前正式 AIOps 架构

### 默认巡检
- 正式入口：`host_health_patrol_profile`
- 默认采集：
  - `get_cpu_summary`
  - `get_memory_summary`
  - `get_disk_usage`
  - `get_patrol_alerts`
- 若主机健康且无告警，则输出主机健康巡检报告。
- 若发现异常，则自动升级到对应专项 Profile。

### 专项诊断
- `cpu_pressure_profile`
- `memory_pressure_profile`
- `disk_pressure_profile`

三类专项诊断统一采用：
- Investigation Runtime
- Evidence Store
- StopController
- Runtime Registry
- Follow-up Context

### 自动升级
默认巡检发现异常后，按优先级自动升级：
1. 最高严重级别 active alert
2. 若无 active alert，则按资源异常级别选择

当前支持映射：
- `HighCPUUsage` / `HostHighCPUUsage` -> `cpu_pressure_profile`
- `HighMemoryUsage` / `HostHighMemoryUsage` / `MemoryPressure` -> `memory_pressure_profile`
- `HighDiskUsage` / `HostHighDiskUsage` / `DiskFull` -> `disk_pressure_profile`

## Follow-up Context

AIOps 多轮追问已接通。

系统会先判断当前问题与上一轮诊断的关系：
- `independent`
- `dependent_followup`
- `ambiguous`

依赖型追问会恢复上一轮压缩上下文：
- 上一轮问题
- 上一轮 Profile
- 上一轮对象
- 上一轮诊断摘要
- 关键证据
- 建议摘要
- Runbook 摘要
- 是否已用外部搜索

## Tavily / web_search

`web_search` 不是默认每次都调用。

只在以下场景受控触发：
1. 本地 Runbook / RAG 不足
2. 用户明确反馈上一轮建议无效

外部结果会明确标记为：
- 外部补充参考
- 不属于本地实时证据

## Action Safety

高风险动作不自动执行。

报告会输出动作分级：
- 可直接给出的低风险建议
- 可 dry-run 的动作
- 需人工确认或审批的动作
- 禁止自动执行的动作

## Heartbeat Patrol

### 配置
- `AIOPS_HEARTBEAT_ENABLED=false`
- `AIOPS_HEARTBEAT_INTERVAL_MINUTES=60`
- `AIOPS_HEARTBEAT_TRIGGER_DEEP_DIAGNOSIS=true`
- `AIOPS_HEARTBEAT_STORE_REPORT=true`
- `AIOPS_HEARTBEAT_MAX_CONCURRENT_RUNS=1`

### 行为
- 启用后，主项目会启动定时心跳巡检。
- 心跳巡检先做轻量主机扫描：
  - `get_cpu_summary`
  - `get_memory_summary`
  - `get_disk_usage`
  - `get_patrol_alerts`
- 若 CPU / 内存 / 磁盘都 healthy 且无活跃告警：
  - 只保存 heartbeat summary
  - 不调用 LLM
  - 不触发 Tavily
  - 不执行 remediation
- 若存在 warning / critical 或 active alert：
  - 复用现有 `host_health_patrol_profile`
  - 自动升级到对应 CPU / Memory / Disk 专项诊断
  - 保存 diagnosis report 与 remediation candidates
  - 不自动 execute

### Heartbeat API
- `POST /api/v1/aiops/heartbeat/run`
- `GET /api/v1/aiops/heartbeat/latest`
- `GET /api/v1/aiops/heartbeat/history`

## Remediation Candidates

### 目标
根据 CPU / Memory / Disk 诊断结果生成候选动作，但第一版不自动执行。

### 报告展示
报告中的 `Remediation Candidates` 会分组展示：
1. 可直接给出的低风险建议
2. 可 dry-run 的动作
3. 需人工确认或审批的动作
4. 禁止自动执行的动作

### Dry-run / Execute API
- `POST /api/v1/aiops/remediation/dry-run`
- `POST /api/v1/aiops/remediation/execute`

### 约束
- dry-run 只做影响评估
- execute 必须带 `approval_token`
- forbidden action 永远拒绝
- reboot server、删除数据库目录、删除持久化卷等动作禁止自动执行

## 关键配置

### LLM
- `LLM_API_BASE`
- `LLM_API_KEY`
- `LLM_MODEL`

### Embedding
- `EMBEDDING_API_BASE`
- `EMBEDDING_API_KEY`
- `TEXT_EMBEDDING_MODEL`
- `MULTIMODAL_EMBEDDING_MODEL`

### Monitor / Alert Provider
- `AIOPS_MONITOR_PROVIDER=mock|remote_host`
- `AIOPS_REMOTE_HOST_BASE_URL=...`
- `AIOPS_REMOTE_HOST_TOKEN=...`
- `AIOPS_ALERT_PROVIDER=mock|remote_host|disabled`

### External Search
- `WEB_SEARCH_ENABLED=true|false`
- `TAVILY_API_KEY=...`
- `WEB_SEARCH_MAX_RESULTS=...`
- `WEB_SEARCH_DEPTH=...`
- `WEB_SEARCH_TIMEOUT=...`

### AIOps Control
- `AIOPS_MAX_STEPS=8`
- `AIOPS_ALLOW_LEGACY_GENERIC_DIAGNOSIS=false`

## 重要约束

新增 AIOps MCP Tool 后，必须同步更新：
- `tool_policy.yaml`

否则即使调试脚本可以直接调用，网页里的 AIOps Executor 仍可能因为默认 `blocked` 而失败。

## 相关文档

- 架构说明：`docs/aiops_investigation_architecture.md`
- 验收矩阵：`docs/aiops_acceptance_test_matrix.md`
