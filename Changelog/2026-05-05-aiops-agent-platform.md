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

## 风险与说明
- 当前未补完整端到端联调；审批续跑、Verifier 触发和评估脚本仍建议在具备完整模型/MCP/向量依赖的环境里再跑一轮真实链路。
- 仓库内仍存在部分历史中文注释编码噪音，本次优先保证新增治理链路和前端交互可维护。
