# AgentAssistant

AgentAssistant 是一个同时支持普通 RAG 知识问答与 AIOps 实时诊断的 Python 项目。

当前正式架构已经完成以下能力：
- 普通 RAG 与 AIOps 模式边界隔离
- CPU / Memory / Disk 专项诊断统一迁移到 Evidence-Driven Investigation Runtime
- 默认巡检升级为主机基础健康巡检
- 巡检发现异常后可自动升级到对应专项 Profile
- AIOps 多轮追问已接通
- 在本地知识不足或用户反馈建议无效时，可受控触发 Tavily / `web_search`
- 高风险动作仅做分级提示，不自动执行

## 两种模式

### 普通 RAG
- 仅用于知识问答、Runbook 查阅、历史案例参考
- 不直接读取实时主机状态
- 如果用户询问“当前 CPU/内存/磁盘/系统状态”，系统会提示切换到 AIOps 模式
- 若引用知识库中的案例或 mock/demo 数据，必须标记为历史示例，不代表当前实时现场

### AIOps
- 面向实时诊断
- 基于 MCP / Host Agent / 本地工具收集现场证据
- 通过 Runtime Registry 进入对应专项 Profile
- 所有结论优先基于本地实时证据，其次才是本地 Runbook / 外部补充参考

## 当前正式 AIOps 架构

### 默认巡检
默认巡检现在的正式入口是：
- `host_health_patrol_profile`

它会至少采集：
- `get_cpu_summary`
- `get_memory_summary`
- `get_disk_usage`
- `get_patrol_alerts`

若 CPU / 内存 / 磁盘均健康，报告会明确说明：
- 当前主机未发现明显资源级异常
- 当前无需执行处置动作
- 若告警源为空，则写明未发现活跃告警

若发现异常，则会自动升级进入专项诊断，而不是只停留在“建议进入专项诊断”。

### 专项诊断 Profile
当前正式支持的 execution profile：
- `cpu_pressure_profile`
- `memory_pressure_profile`
- `disk_pressure_profile`

它们统一走：
- `InvestigationTask`
- `Evidence Store`
- `StopController`
- `Runtime Registry`
- `Verifier`

### 巡检自动升级
默认巡检发现异常后，会按以下优先级选择升级目标：
1. 优先选择活跃告警中的最高严重级别异常
2. 若无活跃告警，则根据 CPU / 内存 / 磁盘的资源异常进行选择

当前支持的映射：
- `HighCPUUsage` / `HostHighCPUUsage` -> `cpu_pressure_profile`
- `HighMemoryUsage` / `HostHighMemoryUsage` / `MemoryPressure` -> `memory_pressure_profile`
- `HighDiskUsage` / `HostHighDiskUsage` / `DiskFull` -> `disk_pressure_profile`

## Evidence-Driven Investigation

专项诊断遵循统一优先级：

### 第一层：本地实时证据
- CPU：
  - `get_cpu_summary`
  - `list_top_cpu_processes`
- Memory：
  - `get_memory_summary`
  - `list_top_memory_processes`
- Disk：
  - `get_disk_usage`
  - `list_large_directories`
  - `list_large_files`
  - `query_docker_disk_usage`
  - `query_deleted_open_files`

### 第二层：本地上下文证据
仅在存在合适上下文时才补查，例如：
- `search_historical_tickets`
- `search_log`
- `get_service_info`

没有 `service_name` 或目标对象时，不会强行去查服务日志或历史工单。

### 第三层：本地 RAG / Runbook
- CPU：`retrieve_knowledge("CPU 使用率过高 排查 runbook")`
- Memory：`retrieve_knowledge("内存使用率过高 排查 runbook")`
- Disk：`retrieve_knowledge("磁盘使用率过高 清理 runbook")`

本地知识库内容只作为参考，不会伪装成实时事实。

## Follow-up Context

AIOps 多轮追问已经接通。

系统会先判定当前问题与上一轮 AIOps 诊断的关系：
- `independent`
- `dependent_followup`
- `ambiguous`

### dependent_followup
例如：
- 为什么你建议先观察热点进程？
- 为什么建议先看 Docker build cache？
- 按你说的做了还是没效果
- 继续查别的方法

这类请求会恢复上一轮的 `previous_aiops_context`，并优先走 follow-up 处理，而不是当作一个全新的诊断请求。

### independent
例如：
- CPU 状况怎么样？
- 内存情况如何？
- 请检查服务器磁盘使用情况

这类问题不会错误复用上一轮诊断上下文。

## Tavily / web_search 触发规则

`web_search` 不是默认每次都会调用。

它只会在以下场景受控触发：

### 场景 1：本地知识不足
例如：
- `retrieve_knowledge` 未返回有效内容
- Runbook 无法支撑新的处理建议
- follow-up resolver 判定需要外部补充参考

### 场景 2：用户反馈上一轮建议无效
例如：
- 按你说的做了还是没效果
- 重新执行后还是不行
- 这个方案没用
- 继续查别的思路

此时系统会：
1. 先恢复上一轮 AIOps 上下文
2. 再由 follow-up resolver 判断是否需要外部搜索
3. 如触发外搜，结果会明确标记为“外部补充参考”

外部资料：
- 不等同于本地实时证据
- 不会改写为已确认的现场事实
- 搜索失败也不会阻塞主流程收口

## 动作安全边界

当前系统不会自动执行高风险动作。

报告会输出三类建议：
- 可直接给出的低风险建议
- 需人工确认或审批的动作
- 禁止自动执行或高风险动作

并明确说明：
- 本轮未执行任何重启、扩容、限流、清理或其他高风险操作
- 若后续接入危险工具，必须经过审批节点

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

### AIOps Monitor / Alert Provider
- `AIOPS_MONITOR_PROVIDER=mock|remote_host`
- `AIOPS_REMOTE_HOST_BASE_URL=...`
- `AIOPS_REMOTE_HOST_TOKEN=...`
- `AIOPS_ALERT_PROVIDER=mock|remote_host|disabled`

### 外部搜索
- `WEB_SEARCH_ENABLED=true|false`
- `TAVILY_API_KEY=...`
- `WEB_SEARCH_MAX_RESULTS=...`
- `WEB_SEARCH_DEPTH=...`
- `WEB_SEARCH_TIMEOUT=...`

### AIOps 控制项
- `AIOPS_MAX_STEPS=8`
- `AIOPS_ALLOW_LEGACY_GENERIC_DIAGNOSIS=false`

## 重要约束

新增 AIOps MCP Tool 后，必须同步更新：
- `tool_policy.yaml`

否则即使 debug 直调成功，网页 Agent 里的 Executor 仍可能因默认 `blocked` 而无法调用。

## 相关文档

- 架构说明：`docs/aiops_investigation_architecture.md`
- 验收矩阵：`docs/aiops_acceptance_test_matrix.md`
