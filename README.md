# SuperBizAgent

SuperBizAgent 是一个基于 `FastAPI + LangGraph + MCP + Milvus` 的 AgentAssistant 项目，当前同时提供：

- 普通 RAG 对话
- 面向 AIOps 的可治理 Agent 诊断链路

## 当前能力

### 普通对话

- `POST /api/chat`
- `POST /api/chat_stream`

普通对话使用本地知识库进行检索增强，不会自动接入 AIOps 专用的外部搜索逻辑。

### AIOps Agent

AIOps 使用独立的 Investigation Engine，支持：

- `host_health_patrol_profile`
- `cpu_pressure_profile`
- `memory_pressure_profile`
- `disk_pressure_profile`

主要治理能力包括：

- Skill Router
- Tool Policy
- Agent Trace
- Human-in-the-loop 审批
- Verifier
- Incident Memory
- 本地 Runbook / RAG
- 受控外部搜索 `web_search`

## 默认巡检

当前默认巡检不再只是“查告警”，而是先执行主机基础健康巡检。

默认会先采集：

- `get_cpu_summary`
- `get_memory_summary`
- `get_disk_usage`
- `get_patrol_alerts`

### 巡检语义

1. 先输出主机 CPU / 内存 / 磁盘的基础健康状态
2. 若未发现明显异常，则直接收口为健康巡检报告
3. 若发现异常，则会构造 `abnormal_findings`
4. 系统自动选择最高优先级异常，升级进入对应专项 Profile

当前升级映射：

- `HighCPUUsage` / `HostHighCPUUsage` -> `cpu_pressure_profile`
- `HighMemoryUsage` / `HostHighMemoryUsage` / `MemoryPressure` -> `memory_pressure_profile`
- `HighDiskUsage` / `HostHighDiskUsage` / `DiskFull` -> `disk_pressure_profile`

也就是说：

- 默认巡检本身先做“主机健康巡检”
- `patrol_dispatch_profile` 现在主要作为内部告警分发能力存在
- 真正的深度专项诊断由 CPU / Memory / Disk Profile 承担

## 专项诊断的证据优先级

专项诊断遵循固定优先级：

1. 本地实时证据
2. 本地上下文证据
3. 本地 Runbook / RAG
4. 外部补充参考

### 本地实时证据

- CPU：`get_cpu_summary`、`list_top_cpu_processes`
- Memory：`get_memory_summary`、`list_top_memory_processes`
- Disk：`get_disk_usage`、`list_large_directories`、`list_large_files` 等

### 本地上下文证据

仅当存在 `service_name` / `alert_name` 等上下文时，才会考虑：

- `get_service_info`
- `search_historical_tickets`
- 其他内部上下文工具

没有 `service_name` 时，不会硬查服务日志或服务工单。

### 本地 Runbook / RAG

专项 Profile 会继续执行本地知识检索，例如：

- CPU：`retrieve_knowledge("CPU 使用率过高 排查 runbook")`
- Memory：`retrieve_knowledge("内存使用率过高 排查 runbook")`
- Disk：`retrieve_knowledge("磁盘使用率过高 清理 runbook")`

### 外部补充参考

项目已有 `web_search` 工具，但它不是每次诊断都调用。

只会在以下两类场景触发：

1. 本地 Runbook / RAG 不足
2. 用户明确反馈已有建议无效

典型反馈包括：

- “我按你说的做了，还是没用”
- “处理后仍然不行”
- “这个办法没有效果”
- “继续查别的解决方案”
- “还有其他办法吗”

外部搜索结果只允许作为 `external_reference` 使用，不得伪装成本地实时证据。

## 危险操作边界

报告中会统一输出“处置动作分级”：

### 可直接给出的低风险建议

- 继续观察
- 补充检查
- 核对指标 / 进程 / 目录 / 文件

### 需人工确认或审批的动作

- restart service
- scale service
- 限流
- 清理 Docker build cache
- 修改服务配置

### 禁止自动执行或高风险动作

- `kill -9` 关键进程
- 删除数据库目录
- `rm -rf` 未确认路径
- 删除持久化卷

系统当前会明确写明：

- 本轮未执行任何重启、清理、扩容、限流或其他高风险操作
- 若后续接入危险操作工具，必须经过审批节点

## Tool Policy

`tool_policy.yaml` 默认会把未知工具视为 `blocked`。

因此每次新增 AIOps MCP Tool 时，都必须同步更新 `tool_policy.yaml`。

尤其是新的只读监控工具，通常要明确写成：

- `read_only`

如果调试脚本能直调成功，但网页 Agent 里失败，优先检查：

- 工具是否已加入 `tool_policy.yaml`
- 工具是否被错误留在默认 `blocked`
- 后端是否在更新后已重启

## Monitor / Alert Provider

### Monitor Provider

用于控制 Host Agent / mock 证据来源：

- `AIOPS_MONITOR_PROVIDER=mock`
- `AIOPS_MONITOR_PROVIDER=remote_host`

### Alert Provider

用于控制默认巡检的告警来源：

- `AIOPS_ALERT_PROVIDER=mock`
- `AIOPS_ALERT_PROVIDER=remote_host`
- `AIOPS_ALERT_PROVIDER=disabled`

`remote_host` 模式下，系统会基于 Host Agent 的摘要接口合成主机级告警，例如：

- `HostHighCPUUsage`
- `HostHighMemoryUsage`
- `HostHighDiskUsage`

## 关键配置

```env
APP_NAME=SuperBizAgent
DEBUG=True
HOST=0.0.0.0
PORT=9900

LLM_API_KEY=
LLM_API_BASE=
LLM_MODEL=

EMBEDDING_API_KEY=
EMBEDDING_API_BASE=
TEXT_EMBEDDING_MODEL=text-embedding-v4
MULTIMODAL_EMBEDDING_MODEL=

WEB_SEARCH_ENABLED=false
TAVILY_API_KEY=
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_DEPTH=basic
WEB_SEARCH_TIMEOUT=10

AIOPS_MAX_STEPS=8
AIOPS_ALLOW_LEGACY_GENERIC_DIAGNOSIS=false

AIOPS_MONITOR_PROVIDER=mock
AIOPS_REMOTE_HOST_BASE_URL=
AIOPS_REMOTE_HOST_TOKEN=

AIOPS_ALERT_PROVIDER=mock
```

## 启动

### 1. 启动向量库

```bash
docker compose -f vector-database.yml up -d
```

### 2. 启动 MCP 服务

```bash
python mcp_servers/cls_server.py
python mcp_servers/monitor_server.py
```

### 3. 启动主服务

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900
```

## 常用入口

- Web UI: [http://localhost:9900](http://localhost:9900)
- Swagger: [http://localhost:9900/docs](http://localhost:9900/docs)

## AIOps 输入示例

### 默认巡检

```text
请开始一次 AIOps 巡检，并保留完整 Agent Trace。
```

### CPU

```text
系统现在 CPU 情况如何？
```

```text
CPU 占用高怎么办？
```

### Memory

```text
系统现在内存情况如何？
```

```text
内存满了怎么办？
```

### Disk

```text
请检查服务器当前磁盘空间使用情况，并分析主要占用来源。
```

## 目录

```text
app/
  api/
  agent/aiops/
  core/
  models/
  services/
  tools/
static/
mcp_servers/
skills/
mock_data/
data/
docs/
aiops-docs/
```

## 当前阶段说明

当前已经完成到 Phase 4.9B：

- Phase 1：架构止血
- Phase 2：Disk Runtime 迁移
- Phase 3：Runtime Registry + Patrol Dispatcher
- Phase 4：CPU / Memory Runtime
- Phase 4.5：真实 Host Alert / CPU 字段兼容
- Phase 4.8A：默认主机健康巡检
- Phase 4.9B：巡检异常自动升级 + 本地优先 + 外搜受控触发 + 处置动作分级

Phase 5 的 legacy 删除尚未开始。
