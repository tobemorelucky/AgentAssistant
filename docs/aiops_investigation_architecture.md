# AIOps Investigation Architecture

## 目标

AgentAssistant 的 AIOps 能力正在从“模板式诊断链”迁移到统一的 Evidence-Driven Investigation Engine。

核心原则：

1. 用 Profile 定义诊断边界
2. 用 Runtime 执行证据驱动流程
3. 用 Evidence Store 约束事实来源
4. 用 StopController 防止无限循环
5. 用 Verifier 校验“事实 / 推断 / 参考 / 缺口”的边界

## 为什么旧架构不稳定

旧 AIOps 同时存在多条不统一链路：

- disk_cleanup 专项确定性分支
- default patrol 模板分支
- custom generic + LLM fallback 长链

典型问题包括：

- Planner 写的步骤和 Executor 实际执行的工具不一致
- Verifier 把自由文本 `suggested_next_steps` 回填成新的计划
- 证据不足时依然生成过度推断结论
- Trace 容易持续增长，难以收口

## 核心构件

### DiagnosisProfile

Profile 定义一类诊断任务的固定边界：

- `profile_id`
- `supported_intents`
- `resource_type`
- `required_evidence_slots`
- `conditional_evidence_slots`
- `reference_evidence_slots`
- `stop_rules`
- `report_schema`

### InvestigationRuntime

Runtime 是某个 Profile 的执行器。当前统一接口包括：

1. `build_initial_tasks`
2. `normalize_result`
3. `update_evidence_store`
4. `build_follow_up_tasks`
5. `decide_stop`
6. `build_report`
7. `verify_report`
8. `summarize_task_result`
9. `summarize_evidence_store`
10. `compute_no_progress_rounds`
11. `build_escalation`

### Evidence Store

Evidence Store 记录每个证据槽的状态：

- `missing`
- `collected`
- `failed`
- `partial`

以及：

- `source`
- `payload`
- `attempts`
- `quality`
- `error_message`

### StopController

StopController 的职责是决定：

- 继续补证据
- 正常收口
- 带限制收口

它阻止：

- 无限补查同一证据槽
- 无进展反复循环
- required evidence 明显失败时仍伪装成“已完成”

## Skill 的新定位

Skill 不再默认等于“执行模板”，而是分成三类：

- `execution_profile`
- `reference_playbook`
- `draft`

含义如下：

- `execution_profile`：允许驱动正式 Investigation Runtime
- `reference_playbook`：只作为知识参考，不直接生成执行计划
- `draft`：不进入正式执行

## 已完成阶段

### Phase 1：架构止血

- 新增 investigation 基础模型
- 扩展 `PlanExecuteState`
- 旧 Skill 默认降级为 `reference_playbook`
- 未命中 `execution_profile` 的 custom AIOps 不再进入旧 generic 长链
- Verifier 不再把自由文本 `suggested_next_steps` 直接回填为 plan

### Phase 2：Disk Runtime

完成 `disk_pressure_profile` 迁移：

- InvestigationTask
- Evidence Store
- Follow-up Tasks
- StopController
- Evidence-grounded Report
- Disk Verifier

### Phase 3：Runtime Registry + Patrol Dispatcher

- 引入统一 runtime registry
- 默认巡检不再直接走 generic 深链
- `patrol_dispatch_profile` 成为内部告警分发能力

### Phase 4：CPU / Memory Runtime

完成：

- `cpu_pressure_profile`
- `memory_pressure_profile`
- `CpuInvestigationRuntime`
- `MemoryInvestigationRuntime`

第一版只聚焦：

- 实时摘要
- 热点进程
- 本地 Runbook / RAG
- 稳定收口

暂不强制引入日志 / 工单 / 更深服务级证据。

### Phase 4.5：真实 Host Alert 与 CPU 字段兼容

- Monitor Provider 支持真实 Host Agent
- CPU summary 兼容 `cpu_percent / logical_cpu_count / load_1m`
- 新增 Alert Provider：
  - `mock`
  - `remote_host`
  - `disabled`

### Phase 4.8A：Host Health Patrol

默认巡检升级为 `host_health_patrol_profile`，先采集：

- `cpu_summary`
- `memory_summary`
- `disk_usage`
- optional `active_alerts`

这一阶段的默认巡检可以输出主机健康结论，但异常时仍主要停留在“建议进入专项诊断”。

### Phase 4.9B：Agentic Escalation + 本地优先 + 外搜受控触发

这是当前最新阶段。

#### 1. 巡检异常自动升级

`host_health_patrol_profile` 在 required evidence 齐备后，会生成：

- `abnormal_findings`
- `selected_escalation_profile`
- `escalation_reason`
- `target_alert`

优先级规则：

1. 若存在 active alert，优先选最高 severity alert
2. 若无 active alert，再按 CPU / Memory / Disk 的异常 severity 排序

随后系统会自动切换到对应专项 Profile，而不是只停留在“建议进入专项诊断”。

#### 2. 本地证据优先级

专项 Profile 按以下顺序工作：

1. 本地实时证据
2. 本地上下文证据
3. 本地 Runbook / RAG
4. 外部补充参考

#### 3. Tavily 外搜触发边界

`web_search` 不是默认步骤。

只在两类场景触发：

1. 本地 Runbook / RAG 不足
   - 无内容
   - 无引用文档
   - 无法形成有效处理建议
2. 用户反馈已有建议无效
   - “还是没用”
   - “处理后仍然不行”
   - “继续查”
   - “还有其他办法吗”

外搜结果必须标记为 `external_reference`，只用于补充思路，不能伪装成本地实时证据。

#### 4. 处置动作分级

专项报告统一输出：

- 低风险建议
- 需人工确认 / 审批的动作
- 禁止自动执行 / 高风险动作

并明确写明：

- 本轮未自动执行危险操作
- 若未来接入危险操作工具，必须经过审批节点

## 当前默认巡检语义

当前默认巡检已经变成：

1. 执行主机健康巡检
2. 若健康，则直接输出健康结论
3. 若异常，则自动选择最高优先级异常
4. 升级进入 CPU / Memory / Disk 对应专项 Profile
5. 继续执行本地实时证据 + 本地知识优先的专项诊断

## 当前可执行 Profile

- `host_health_patrol_profile`
- `cpu_pressure_profile`
- `memory_pressure_profile`
- `disk_pressure_profile`

## 仍保留的 legacy

目前仍保留但不作为默认主路径的 legacy 包括：

- old `disk_cleanup` 兼容逻辑
- old patrol 深链兼容逻辑
- old generic fallback compatibility

这些逻辑将在 Phase 5 再统一清理，不在当前阶段贸然删除。

## Phase 5 之后再做什么

当前不进入 Phase 5。

进入 Phase 5 的前提是：

- 默认巡检 + CPU / Memory / Disk 专项诊断已稳定
- 自动升级链条已稳定收口
- 外搜边界和审批边界已验证
- legacy 路径已不再被主链依赖

届时再删除：

- 旧 disk_cleanup 主入口
- 旧 patrol 深链
- 旧 generic fallback 主路径
