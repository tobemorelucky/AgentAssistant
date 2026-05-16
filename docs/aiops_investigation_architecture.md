# AIOps Investigation Architecture

## 背景

当前 AgentAssistant 的 AIOps 能力经历了三步演进：

1. 早期实现同时存在多条并行链路：
   - `disk_cleanup` 专项确定性分支
   - `default patrol` 默认巡检深诊断分支
   - `custom generic` 的 Planner + LLM fallback 长链
2. 这几条链路各自维护计划、执行、补查和报告逻辑，导致：
   - 计划与执行脱节
   - Verifier 打回后容易把自由文本建议重新塞回 plan
   - 同类问题在不同入口走不同执行语义
   - Trace 与报告收口不一致
3. 因此需要把 AIOps 逐步迁到统一的 Evidence-Driven Investigation Engine。

---

## Phase 1：架构止血

Phase 1 的目标不是统一所有流程，而是先阻止旧 generic 长链继续放大问题。

已经完成：

- 新增基础数据模型：
  - `DiagnosisIntent`
  - `DiagnosisProfile`
  - `EvidenceRecord`
  - `InvestigationTask`
  - `StopDecision`
- 扩展 `PlanExecuteState`，加入：
  - `diagnosis_intent`
  - `selected_profile`
  - `evidence_store`
  - `investigation_round`
  - `no_progress_rounds`
  - `stop_decision`
- Skill 语义重构：
  - `execution_profile`
  - `reference_playbook`
  - `draft`
- 默认关闭 legacy generic 深链：
  - `AIOPS_ALLOW_LEGACY_GENERIC_DIAGNOSIS=false`
- 对没有命中 `execution_profile` 的 custom AIOps：
  - 不再进入旧的 generic 深度自主链路
  - 改为受控结束并说明当前缺少结构化 Profile

Phase 1 的核心原则：

- 宁可暂时少诊断，也不要继续生成不受控的长链。
- `reference_playbook` 只能作为参考知识，不能直接驱动执行计划。

---

## Phase 2：disk_pressure_profile 迁移

磁盘诊断是第一个正式迁移到新引擎的 Profile。

### Profile 定义

- `profile_id = "disk_pressure_profile"`
- required evidence:
  - `disk_usage`
  - `large_directories`
  - `large_files`
- conditional evidence:
  - `docker_disk_usage`
  - `deleted_open_files`
- reference evidence:
  - `disk_runbook`

### 已实现能力

- `InvestigationTask`
- `Evidence Store`
- Follow-up task 生成
- `StopController`
- 基于 evidence 的最终报告
- Disk 专属 verifier

### 结果

磁盘诊断现在不再主要依赖 `past_steps` 自由拼接，而是围绕 `evidence_store` 运转：

- Planner 生成结构化任务
- Executor 只执行指定工具
- Replanner 按 evidence 缺口决定是否补查
- Verifier 校验“引用的事实是否真实存在于 evidence_store”

旧 `disk_cleanup` 分支仍保留，但只作为 legacy 兼容层。

---

## Phase 3：Runtime Registry + Patrol Dispatcher

Phase 3 的目标有两个：

1. 把磁盘专项的 disk-specific engine 抽象成通用 runtime 机制；
2. 把默认巡检从“深诊断模板链”改成“告警发现 + Profile 分发入口”。

### 1. Runtime Registry

新增：

- `app/agent/aiops/investigation/runtime.py`

统一定义 `InvestigationRuntime` 接口，至少包含：

- `build_initial_tasks(state)`
- `update_evidence_store(state, task, raw_result)`
- `build_follow_up_tasks(state)`
- `decide_stop(state)`
- `build_report(state)`
- `verify_report(state)`

同时加入 runtime registry：

- `get_runtime(profile_id)`
- `has_runtime(profile_id)`

当前首个正式 runtime：

- `DiskInvestigationRuntime`

它只是把 Phase 2 已经稳定的磁盘逻辑包装进统一接口，不改变原有行为。

### 2. Patrol Dispatcher

默认巡检现在的职责不再是直接做深诊断，而是：

1. 发现活跃告警
2. 选出最高严重级别告警
3. 将告警分发到可执行 Profile
4. 如果没有匹配到 Profile，则受控结束

当前映射：

- `HighDiskUsage` / `DiskFull` → `disk_pressure_profile`
- `HighCPUUsage` → 当前尚未实现对应 runtime，输出 controlled unsupported-profile result
- 其他告警 → 同样受控结束

### Patrol Dispatcher 的意义

这样 default patrol 不再维护自己的深诊断模板链，而只负责：

- alert discovery
- target alert selection
- profile dispatch

真正的深诊断能力由各自 Profile Runtime 承担。

---

## 当前架构分层

### Diagnosis Profile 层

定义“某类问题需要哪些证据槽，以及何时可以收口”。

当前已有：

- `patrol_dispatch_profile`
- `disk_pressure_profile`

### Runtime 层

负责把 profile 变成真正可执行的运行时策略。

当前已有：

- `DiskInvestigationRuntime`

### Engine State 层

统一的状态字段已经包括：

- `selected_profile`
- `evidence_store`
- `investigation_round`
- `no_progress_rounds`
- `stop_decision`

### Legacy Compatibility 层

仍保留，但已不是默认主入口：

- 旧 `disk_cleanup` 分支
- 旧 default patrol 深诊断链
- 旧 generic fallback 链

这些分支当前都应该被标记为 legacy，计划在后续阶段逐步删除。

---

## 为什么 Phase 3 不直接做 CPU / Memory

Phase 3 先抽象 runtime，而不是立刻继续迁 CPU / Memory，原因是：

1. 如果没有通用 runtime registry，继续迁第二个 Profile 会复制更多 disk-specific if/else。
2. default patrol 如果还保留自己的深诊断模板链，后续 CPU / Memory 迁入后仍然会出现“双入口、双语义”。
3. 先让：
   - Planner
   - Executor
   - Replanner
   - Verifier

   都学会通过 `get_runtime(profile_id)` 工作，后面迁更多 Profile 才不会继续分叉。

---

## Phase 4 规划

下一阶段优先迁移：

### `cpu_pressure_profile`

建议 required evidence：

- `cpu_metrics`
- `process_list`
- `service_logs`
- `historical_tickets`

conditional evidence：

- `memory_metrics`
- `runbook_reference`

### `memory_pressure_profile`

建议 required evidence：

- `memory_metrics`
- `process_list`
- `service_logs`

conditional evidence：

- `container_memory`
- `gc_or_heap_signal`
- `historical_tickets`

这两个 Profile 迁移时，可直接复用：

- `DiagnosisProfile`
- `InvestigationRuntime`
- `evidence_store`
- `StopController`
- runtime-based report / verifier flow
- patrol dispatcher 的 alert → profile 映射

---

## Phase 5 规划

在 CPU / Memory 等核心 Profile 迁移稳定后，再删除 legacy 分支：

- legacy `disk_cleanup`
- legacy patrol 深诊断模板链
- 旧 generic fallback 深链兼容逻辑

Phase 5 的目标是：

- 所有可执行 AIOps 深诊断都走 `execution_profile + runtime registry`
- `reference_playbook` 只保留知识参考角色
- default patrol 永远只做 discovery + dispatch

---

## 当前结论

截至 Phase 3：

- default patrol 的新职责已经明确：
  - alert discovery
  - profile dispatch
- 磁盘诊断已经成为第一条真正跑在统一 Investigation Engine 上的正式链路
- 其余场景在没有对应 execution_profile 时，应该优先受控结束，而不是继续进入旧的自由长链
