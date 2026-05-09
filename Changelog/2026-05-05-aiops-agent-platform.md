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
- 修复磁盘诊断链路中的状态容错问题：AIOps 编排层和 incident memory 现在会兼容 `matched_skills` 为字典或字符串列表的情况，同时磁盘报告构建对 list/dict 结果做了更稳的兜底，避免出现 `'list' object has no attribute 'get'`。
- 精简前端侧栏：移除了页面左下角的 Skill 草稿区域及其空态占位，只保留会话历史；Skill 草稿仍只在“是否帮助到您”反馈为“是”后由后端生成。
- 清理前端摘要展示：执行步骤摘要和 Trace 参数现在会过滤掉 `type`、`test`、`data` 等原始结构噪声，优先展示磁盘使用率、Top 目录、大文件、Docker 占用等更可读的摘要信息。
- 调整 AIOps 报告卡片布局：限制横向溢出，强化 `pre`/表格/Trace 的自动换行与纵向排列，避免页面在报告较长时出现左右拖拽；同时固定 `查看 Agent Trace` 在上、`是否帮助到您` 反馈区在下的顺序。
- 新增 `.env` 配置 `AIOPS_MAX_STEPS=8`，并接入后端 `replanner` 的最大执行步数控制，便于按环境调整诊断深度。
- 修复 `disk_cleanup` AIOps 链路中的证据解析问题：Executor 现在会把 MCP 文本块结果先还原成结构化 JSON，再对 `get_disk_usage`、`list_large_directories`、`list_large_files`、`query_deleted_open_files`、`query_docker_disk_usage`、`get_disk_cleanup_candidates` 生成字段级摘要，避免前端只看到“共 1 项结果”。
- 重写磁盘诊断报告生成与 Verifier 规则：报告不再输出 `unknown%` / `unknownGB` 占位，而是基于真实工具证据输出具体数值；缺失字段会明确写成“该字段未返回”；Verifier 对 `unknown`、证据与结论矛盾、Docker/目录/文件证据不足、cleanup_candidates 为空等情况会直接判定不通过。
- 修复默认 AIOps 巡检在执行阶段报 `"'error'"` 的问题：默认巡检现在改为“固定告警发现 + 结构化 ToolPlanStep + Tool Policy 执行 + Replanner 补证据 + Verifier 校验”的受控自主链路，避免再次落回旧的自由 tool-calling 分支。
- 默认巡检增强为确定性编排：第一步固定调用 `get_active_alerts` / `list_active_alerts` 选出最高 severity 的 `target_alert`；随后由 Planner 基于 `target_alert`、命中的 Skill、可用工具、Tool Policy 和 required evidence 生成 4-8 个结构化工具步骤，Executor 直接按步骤执行，前端也已支持把结构化计划渲染成可读文本。
- 为 AIOps Agent 新增可配置的 `web_search` 联网搜索工具：基于 Tavily Search API，通过 `WEB_SEARCH_ENABLED` / `TAVILY_API_KEY` 按 `.env` 开关启用，仅注册到 AIOps 本地工具列表，不接入普通 `rag_agent_service.py`。
- 联网搜索证据链打通到 AIOps Planner / Replanner / Verifier：当本地 Runbook 不足时可以补充 `web_search`，但只能作为外部公开资料参考，不能替代本地监控、日志、工单证据；若使用了联网资料，最终报告会新增“联网搜索补充资料”段落，并要求包含标题、链接、摘要和用途说明。
- 修复自定义 AIOps 诊断在通用 Planner 路径上报 `"'error'"` 的问题：为通用 Planner 增加异常回退计划，并加强 `/api/aiops` SSE 事件的安全序列化，避免单条异常事件或结构化输出异常直接打断整条诊断流。
- 继续修复自定义诊断 `docker镜像冲突怎么办` 这类场景的 `"'error'"` 崩溃：日志显示问题发生在 Planner 模板兜底后的 Executor 自由 tool-calling 分支，因此为 `generic_template_fallback` 增加了专用的确定性执行/汇总/报告/Verifier 链路。现在模板兜底计划会先打上 `plan_source` 标记，Executor 会直接执行 `retrieve_knowledge` / `web_search` 或输出基于已收集证据的整理说明，Replanner 会在计划耗尽后生成稳定的 Markdown 报告，Verifier 也会专门检查“是否明确声明未执行危险操作”“是否正确区分联网资料”等最低要求，不再依赖同一条可能失效的模型调用链。
- 修复模板兜底链路的自我循环问题：`generic_template_fallback` 在 Verifier 不通过时不再把同一条文字建议反复回灌成新计划；同时统一了模板报告与 Verifier 对“未执行任何危险操作”的措辞识别，并跳过对“已整理当前证据...”这类摘要的递归引用，避免 Agent Trace 和步骤摘要指数级膨胀。
- 为 `runtime_store` 增加持久化瘦身：限制 `past_steps`、`trace_events`、`response`、`verifier_result` 的持久化长度，避免长会话把 `runtime_sessions/<session_id>.json` 持续写大并再次触发 Windows 文件写入异常。
- 梳理普通 RAG Chat 与 AIOps 的 LLM 配置来源：新增独立的 `LLM_API_KEY`、`LLM_API_BASE`、`LLM_MODEL` 读取逻辑，并让 `rag_agent_service`、AIOps `planner/executor/replanner/verifier` 统一通过 `llm_factory.create_qwen_chat_model()` 创建模型实例，避免继续把旧的 `RAG_MODEL` 与另一家厂商的 `API key/base` 混用导致 401 鉴权错误。
- 调整 AIOps 最终报告渲染：前端最终态不再把“模式 / 任务 / 当前状态 / 诊断计划 / 执行步骤 / Verifier”继续包在报告正文外层，而是只展示后端生成的最终 Markdown 报告；同时收紧通用模板报告内容，去掉重复的任务段，保留结论、证据、建议和风险提示。
