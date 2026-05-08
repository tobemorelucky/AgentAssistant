# 2026-05-05 AIOps Agent 平台工程化改造

## 新增能力
- 引入项目级 `AGENT.md`、`skills/`、`tool_policy.yaml`，建立 Agent Profile、Runbook Skill 和工具分级治理。
- 将 AIOps Agent 能力拆分到 `app/agent/aiops/`，新增 `profile_loader`、`skill_loader`、`skill_router`、`tool_policy`、`trace`、`runtime_store`、`verifier`、`incident_memory`、`skill_draft_generator` 等模块。
- 保持现有 `POST /api/aiops` SSE 兼容，新增 `trace`、`approval_required`、`verifier_result` 事件。
- 新增 `app/api/agent.py`，提供审批接口和 Skill 草稿管理接口。
- 前端新增 Agent Trace 时间线、危险工具审批弹窗、Skill 草稿面板和草稿查看/启用/删除交互。
- 新增 `eval/aiops_cases.jsonl` 与 `eval/run_aiops_eval.py`，用于 AIOps 回归评估。

## 接口与行为变化
- `/api/upload` 响应新增 `indexed` 和 `index_error` 字段，用于区分上传成功与索引成功。
- `dangerous` 工具调用会挂起到 `data/pending_actions/`，审批后以同一 `session_id` 续跑。
- Agent Trace 会落到 `data/agent_traces/<session_id>.jsonl`，Incident Memory 会落到 `data/incident_memory/incidents.jsonl`。

## 质量补充
- 修复 Skill Draft 生成在 `matched_skills` 为空时的越界问题。
- 为 Skill Router、Tool Policy、Runtime Store 增加轻量级测试样例。
- 已执行语法检查：`python -m py_compile ...`、`node --check static/app.js`。
- 更新 `README.md`，补充中文的新能力说明、审批接口、Skill Draft 接口、Agent Trace 与上传响应增强说明。
- 修复前端 `AI Ops` 触发链路，重建 `static/app.js` 的消息渲染、空态切换、SSE 处理与审批续跑逻辑，并为 `/api/aiops` 增加首个初始化状态事件。
- 修复 AIOps 进行中消息在窗口切换或重绘后丢失的问题：前端现在会把进行中内容和 Trace 一并写入当前会话历史，并在重绘时恢复该消息。
- 调整前端交互细节：模式下拉切换点击更稳定；空输入点击发送按钮时不再弹出异常提示；当没有任何 Trace 时不再显示 `Agent Trace 0` 面板。
- 修复上传文本索引时误用多模态 Embedding 模型的问题：新增 `.env` 配置 `DASHSCOPE_EMBEDDING_MODE`、`DASHSCOPE_TEXT_EMBEDDING_MODEL`、`DASHSCOPE_MULTIMODAL_EMBEDDING_MODEL`，并让文本索引、检索、Incident Memory 固定走文本 Embedding 配置。
- 为文本 Embedding 增加误配保护：如果把视觉/多模态模型填到文本模型位，会直接给出明确配置错误，而不是返回难定位的 OpenAI 兼容 404。
- Embedding 链路改为支持独立厂商配置：新增 `.env` 配置 `EMBEDDING_API_KEY`、`EMBEDDING_API_BASE`、`TEXT_EMBEDDING_MODEL`、`MULTIMODAL_EMBEDDING_MODEL`，向量模型现在可以与问答模型使用不同的 URL、API Key 和模型厂商。
- 重构 AIOps 触发逻辑，支持“默认巡检模式”和“自定义诊断模式”：默认模式会先通过 `get_active_alerts` / `list_active_alerts` 获取活跃告警，再围绕最高严重级别告警生成服务级诊断计划；自定义模式则直接使用用户输入任务。
- 修复前端 AIOps SSE 解析与渲染：兼容 `\n\n` 和 `\r\n\r\n` 分隔，逐条处理 `status`、`trace`、`plan`、`step_complete`、`report`、`verifier_result`、`complete`、`error`，并在浏览器控制台输出 `[AIOps SSE]` 调试日志。
- 为 monitor mock 增加 `get_active_alerts`、`list_active_alerts`、`query_process_list`、`search_historical_tickets`、`get_service_info`、`list_all_services`，并把活跃告警工具加入 `tool_policy.yaml` 的 `read_only` 分级。
- 调整前端 Agent Trace 可视化：默认仍保留完整 trace 数据，但不再把 `memory` 节点计入可见 Trace 时间线和计数，避免最终报告已经输出后计数仍继续增长的困惑。
- 调整前端 Agent Trace 交互：Trace 面板默认不展开，只保留“查看 Agent Trace”按钮，点击后才显示详细时间线。
- 调整 Skill Draft 生成时机：诊断完成后只保存 incident memory，不再自动生成草稿；前端会在最终报告底部显示“请问是否帮助到您（是/否）”，只有点击“是”时才通过 `/api/agent/session-feedback` 触发 Skill Draft 生成。

## 风险与说明
- 当前未补完整端到端联调；审批续跑、Verifier 触发和评估脚本仍建议在具备完整模型/MCP/向量依赖的环境里再跑一轮真实链路。
- 仓库内仍存在部分历史中文注释编码噪音，本次优先保证新增治理链路和前端交互可维护。
- 新增基于纯模拟数据的 `disk_cleanup` AIOps Skill 测试链路：补充 `mock_data/disk.json`、`skills/disk_cleanup/SKILL.md`、`mcp_servers/monitor_server.py` 中的只读磁盘诊断工具，以及 `tool_policy.yaml` 的磁盘工具 `read_only` 分级。
- 为磁盘诊断引入确定性的 Planner / Executor / Replanner 逻辑：命中 `disk_cleanup` 后会按固定顺序采集 `get_disk_usage`、`list_large_directories`、`list_large_files`、`query_deleted_open_files`、`query_docker_disk_usage`、`get_disk_cleanup_candidates` 和 `retrieve_knowledge`，最终生成包含具体数值、风险提示和禁止自动清理项的证据型报告。
- 新增轻量测试覆盖磁盘链路：Skill Router 命中 `disk_diagnosis`、磁盘诊断计划顺序、以及最终报告中关键数值与安全段落的断言。
