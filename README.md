# SuperBizAgent

一个面向企业知识问答与 AIOps 场景的 Agent Assistant 项目，基于 `FastAPI + LangGraph + MCP + Milvus` 构建。

当前项目包含两条主能力：

- 普通 RAG 对话：用于本地知识库问答与文档检索
- AIOps Agent：用于巡检、告警诊断、证据采集、风险提示和经验沉淀

## 项目定位

当前版本已经不只是一个简单的 RAG Demo，而是一个更偏 Agent 工程化的 AIOps Agent 平台，重点能力包括：

- Agent Workflow
- Skill Router
- Tool Policy 治理
- Human-in-the-loop 审批
- Agent Trace 执行轨迹
- Verifier 证据校验
- Incident Memory
- Skill Draft
- AIOps 专用联网搜索

## 主要能力

### 普通 RAG 对话

- 支持普通问答与流式问答
- 支持上传文档并写入向量库
- 检索结果来自本地知识库
- 不接入 `web_search`

说明：

- `aiops-docs/` 中的文档会作为本地 Runbook / 知识库内容被检索
- 它不是实时日志，也不是实时监控数据

### AIOps Agent

`POST /api/aiops` 保持流式 SSE 接口兼容，支持以下事件：

- `status`
- `trace`
- `plan`
- `step_complete`
- `report`
- `verifier_result`
- `approval_required`
- `complete`
- `error`

AIOps 支持两种模式：

- 默认巡检模式
- 自定义诊断模式

#### 默认巡检模式

当前端点击 `AI Ops` 且输入框为空时，会自动使用默认任务：

> 请检查当前系统是否存在活跃告警。如果存在告警，请选择最高严重级别告警，结合监控指标、日志、历史工单和知识库 runbook 进行根因分析，并保留完整 Agent Trace。

默认巡检的确定性第一步是：

- `get_active_alerts` / `list_active_alerts`

行为说明：

- 如果没有活跃告警，会直接生成“当前未检测到活跃告警”的巡检报告
- 如果存在活跃告警，会围绕最高严重级别的 `target_alert` 展开后续诊断

#### 自定义诊断模式

当前端输入框有内容时，点击 `AI Ops` 会把输入内容作为 `task` 传给后端。

示例：

```text
data-sync-service 出现 HighCPUUsage 告警，请排查
```

```text
服务器磁盘使用率过高，怀疑硬盘满了，请给出清理建议
```

```text
docker镜像冲突怎么办
```

## Agent 工程化能力

### 1. AGENT.md

项目根目录下的 `AGENT.md` 用作 AIOps Agent 的项目级 Profile，约束：

- 系统定位
- 诊断原则
- 工具使用规范
- 报告格式
- 安全边界

### 2. Skill Router

项目支持 `skills/<skill>/SKILL.md` 形式的本地 Skill。

当前 Skill Router 会根据用户任务、关键词、告警或意图匹配技能，并只把命中的 Skill 注入 Planner。  
当前已接入的典型链路包括：

- 默认告警巡检
- `disk_cleanup` 磁盘清理诊断

### 3. Tool Policy

项目根目录下的 `tool_policy.yaml` 会对工具进行分级：

- `read_only`
- `low_risk`
- `dangerous`
- `blocked`

执行规则：

- `read_only / low_risk`：自动执行
- `dangerous`：进入人工审批
- `blocked`：直接拒绝

### 4. Agent Trace

AIOps 运行过程中会记录完整执行轨迹，包括：

- `planner`
- `skill_router`
- `executor`
- `tool_call`
- `replanner`
- `verifier`
- `approval`
- `memory`

落盘位置：

- `data/agent_traces/<session_id>.jsonl`

前端行为：

- 默认折叠 Agent Trace
- 点击“查看 Agent Trace”后展开

### 5. Verifier

最终报告生成前，Verifier 会检查：

- 是否有足够证据支持结论
- 是否存在无根据推断
- 是否遗漏影响范围
- 是否缺少风险提示
- 是否明确说明未执行危险操作

### 6. 审批

对 `dangerous` 工具调用，后端会挂起执行并等待前端审批。

接口：

- `POST /api/agent/approve`
- `POST /api/agent/reject`
- `GET /api/agent/pending-actions/{session_id}`

### 7. Incident Memory 与 Skill Draft

AIOps 完成后支持沉淀：

- 用户任务
- 命中的 Skill
- 调用过的工具
- 关键证据
- 根因
- 建议
- Verifier 结果

当前行为说明：

- 报告生成后不会立刻自动写入记忆
- 前端会显示“请问是否帮助到您？”
- 只有用户点击“是”后，才会触发后续记忆沉淀与 Skill Draft 生成

## AIOps 专用联网搜索

项目已支持 AIOps 专用 `web_search` 工具，基于 Tavily Search API。

用途：

- 本地 Runbook 不足时补充公开资料
- 查询官方错误码说明
- 查询框架或云厂商公开排障文档

限制：

- 只接入 AIOps
- 不接入普通 RAG Chat
- 联网资料只能作为补充证据，不能替代本地监控、日志、工单和知识库证据

如果报告使用了联网资料，应该在最终报告中明确区分：

- 本地监控/日志/工单证据
- 本地知识库 Runbook
- 联网搜索补充资料

## Mock 数据链路

项目内已经接入多条本地 mock 数据链路，便于联调：

- 活跃告警 mock：`mcp_servers/monitor_server.py`
- 磁盘诊断 mock：`mock_data/disk.json`

当前磁盘监控工具也支持切换到远程 Host Agent 真实数据源：

- `AIOPS_MONITOR_PROVIDER=mock`
- `AIOPS_MONITOR_PROVIDER=remote_host`

当切换为 `remote_host` 时，当前第一轮已经支持以下 3 个工具走远程 Ubuntu Host Agent：

- `get_disk_usage`
- `list_large_directories`
- `query_docker_disk_usage`

对应远程接口：

- `GET /api/v1/disk/usage`
- `GET /api/v1/disk/large-directories`
- `GET /api/v1/docker/disk-usage`

例如输入：

```text
服务器磁盘使用率过高，怀疑硬盘满了，请给出清理建议
```

会命中 `disk_cleanup` Skill，并按顺序采集：

- `get_disk_usage`
- `list_large_directories`
- `list_large_files`
- `query_deleted_open_files`
- `query_docker_disk_usage`
- `get_disk_cleanup_candidates`
- `retrieve_knowledge`

## 目录结构

```text
app/
  api/                   API 路由
  agent/aiops/           AIOps Agent 模块
  core/                  LLM / Milvus 等基础能力
  models/                请求与响应模型
  services/              RAG / AIOps / 向量服务
  tools/                 本地工具（含 web_search）
static/                  前端页面
mcp_servers/             本地 MCP mock 服务
skills/                  本地 Skill 定义
mock_data/               模拟数据
data/                    Trace / 审批 / 运行时 / 记忆落盘目录
aiops-docs/              本地 Runbook / RAG 文档
```

## 快速开始

### 1. 安装依赖

```bash
pip install -e .
```

或者：

```bash
uv venv
uv pip install -e .
```

### 2. 配置 `.env`

建议至少配置以下项目：

```env
APP_NAME=SuperBizAgent
DEBUG=True
HOST=0.0.0.0
PORT=9900

# 聊天 / AIOps 主模型
LLM_API_KEY=
LLM_API_BASE=
LLM_MODEL=

# 兼容旧配置时的回退项
DASHSCOPE_API_KEY=
DASHSCOPE_API_BASE=
DASHSCOPE_MODEL=

# 向量模型
EMBEDDING_API_KEY=
EMBEDDING_API_BASE=
EMBEDDING_MODE=single_modal
TEXT_EMBEDDING_MODEL=text-embedding-v4
MULTIMODAL_EMBEDDING_MODEL=tongyi-embedding-vision-flash-2026-03-06

# Milvus
MILVUS_HOST=localhost
MILVUS_PORT=19530
MILVUS_TIMEOUT=10000

# RAG / AIOps
RAG_TOP_K=3
RAG_MODEL=qwen3.5-plus-2026-02-15
AIOPS_MAX_STEPS=8

# AIOps Web Search
WEB_SEARCH_ENABLED=false
TAVILY_API_KEY=
WEB_SEARCH_MAX_RESULTS=5
WEB_SEARCH_DEPTH=basic
WEB_SEARCH_TIMEOUT=10

# 文档切分
CHUNK_MAX_SIZE=800
CHUNK_OVERLAP=100

# MCP
MCP_CLS_TRANSPORT=streamable-http
MCP_CLS_URL=http://localhost:8003/mcp
MCP_MONITOR_TRANSPORT=streamable-http
MCP_MONITOR_URL=http://localhost:8004/mcp

# AIOps Monitor Provider
AIOPS_MONITOR_PROVIDER=mock
AIOPS_REMOTE_HOST_BASE_URL=
AIOPS_REMOTE_HOST_TOKEN=
```

说明：

- 普通聊天和 AIOps 主模型优先读取 `LLM_*`
- 向量模型优先读取 `EMBEDDING_*`
- 不建议把多模态模型填到 `TEXT_EMBEDDING_MODEL`
- `AIOPS_MONITOR_PROVIDER=remote_host` 时，Monitor MCP 会改为请求远程 Host Agent
- `AIOPS_REMOTE_HOST_TOKEN` 当前按 `X-Host-Agent-Token: <token>` 方式发送；如果远程 Agent 不需要认证，可留空

### 3. 启动依赖

示例：

```bash
docker compose -f vector-database.yml up -d
python mcp_servers/cls_server.py
python mcp_servers/monitor_server.py
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900
```

如果你要读取 Ubuntu 虚拟机中的真实磁盘监控数据，请额外配置：

```env
AIOPS_MONITOR_PROVIDER=remote_host
AIOPS_REMOTE_HOST_BASE_URL=http://192.168.6.129:9001
AIOPS_REMOTE_HOST_TOKEN=
```

### 4. 打开页面

- Web UI: `http://localhost:9900`
- Swagger: `http://localhost:9900/docs`

## 主要接口

### 普通问答

- `POST /api/chat`
- `POST /api/chat_stream`

### AIOps

- `POST /api/aiops`

请求体示例：

```json
{
  "session_id": "session-123",
  "task": "data-sync-service 出现 HighCPUUsage 告警，请排查",
  "mode": "custom"
}
```

说明：

- `mode=default`：默认巡检
- `mode=custom`：自定义诊断

### 上传文档

- `POST /api/upload`

响应里会包含：

- `indexed`
- `index_error`

用于区分：

- 文件上传成功
- 向量索引是否成功

### 审批接口

- `POST /api/agent/approve`
- `POST /api/agent/reject`
- `GET /api/agent/pending-actions/{session_id}`

### 反馈接口

- `POST /api/agent/session-feedback`

说明：

- 前端报告结束后会显示“请问是否帮助到您？”
- 只有点击“是”，才会触发记忆沉淀与 Skill Draft 生成

## 当前前端行为

- 普通对话与 AIOps 分开处理
- AIOps 报告完成后默认显示最终 Markdown 报告
- Agent Trace 默认折叠
- 报告底部显示“请问是否帮助到您？”

## 注意事项

1. `web_search` 只给 AIOps 使用，不接入普通 RAG Chat。
2. `aiops-docs` 是本地 Runbook 文档，不是实时日志。
3. 危险操作只能作为建议展示，不能声称已经执行。
4. 调试 AIOps 时建议使用新的 `session_id`，避免复用旧 runtime snapshot。

## 变更记录

详细工程化改动见：

- `Changelog/2026-05-05-aiops-agent-platform.md`
