# AIOps Investigation Architecture

## 背景

当前 AgentAssistant 的 AIOps 诊断能力，正在从多条彼此分裂的历史链路，逐步迁移到统一的 Evidence-Driven Investigation Engine。

历史上并存过三类路径：

1. `disk_cleanup` 专项确定性分支
2. `default patrol` 模板化巡检分支
3. `custom generic` 的自由 Planner / Executor / Verifier 长链

这些路径曾经带来以下问题：

- Planner 写出来的步骤和 Executor 实际执行工具不一致
- Verifier 把自由文本建议重新塞回 plan，导致 Trace 持续增长
- 旧 Skill 会把经验性排障步骤直接塞进执行计划，容易在证据不足时过度推断
- 默认巡检和专项诊断无法共享同一套 evidence / stop / verifier 机制

重构目标是把 AIOps 诊断统一成：

- DiagnosisProfile
- InvestigationRuntime
- Evidence Store
- StopController
- Evidence-grounded Report
- Runtime-scoped Verifier

---

## Phase 1：架构止血

Phase 1 完成了统一架构的最小骨架，并先止住旧 generic 长链带来的不稳定性。

### 新增基础模型

- `DiagnosisIntent`
- `DiagnosisProfile`
- `EvidenceRecord`
- `InvestigationTask`
- `StopDecision`

### 扩展状态

`PlanExecuteState` 新增：

- `diagnosis_intent`
- `selected_profile`
- `evidence_store`
- `investigation_round`
- `no_progress_rounds`
- `stop_decision`

### Skill 重新定位

Skill 被降级为三种模式：

- `execution_profile`
- `reference_playbook`
- `draft`

其中：

- `execution_profile` 才允许进入正式执行链
- `reference_playbook` 只能作为知识参考
- `draft` 不进入正式执行

### Legacy Generic 止血

新增配置：

- `AIOPS_ALLOW_LEGACY_GENERIC_DIAGNOSIS=false`

默认关闭旧 generic 深链。

效果是：

- 如果 custom AIOps 没命中 `execution_profile`
- 不再进入自由 LLM 长链
- 直接输出受控结果，说明当前尚未接入对应结构化 Profile

---

## Phase 2：disk_pressure_profile 迁移完成

磁盘诊断是第一个正式迁移到新引擎的 Profile。

### Profile

- `profile_id = "disk_pressure_profile"`

required evidence:

- `disk_usage`
- `large_directories`
- `large_files`

conditional evidence:

- `docker_disk_usage`
- `deleted_open_files`

reference evidence:

- `disk_runbook`

### 已落地能力

- 结构化 `InvestigationTask`
- Evidence Store
- Follow-up Tasks
- StopController
- Evidence-grounded Report
- Disk Runtime Verifier

### 结果

磁盘诊断不再围绕 `past_steps` 拼接报告，而是基于 `evidence_store` 决定：

- 继续补查
- 受限收口
- 完整收口

---

## Phase 3：Runtime Registry + Patrol Dispatcher

Phase 3 的目标是把“某个 Profile 的专项逻辑”从主流程里抽出来，并把默认巡检改造成统一分发入口。

### Runtime Registry

新增：

- `app/agent/aiops/investigation/runtime.py`

统一定义 `InvestigationRuntime` 接口：

1. `build_initial_tasks(state)`
2. `update_evidence_store(state, task, raw_result)`
3. `build_follow_up_tasks(state)`
4. `decide_stop(state)`
5. `build_report(state)`
6. `verify_report(state)`
7. `normalize_result(task, raw_result)`
8. `summarize_task_result(task, normalized_result)`
9. `summarize_evidence_store(state)`
10. `compute_no_progress_rounds(state)`

首个 runtime：

- `DiskInvestigationRuntime`

### Patrol Dispatcher

默认巡检不再承诺“深诊断模板链”，而是只负责：

1. 活跃告警发现
2. 目标告警选择
3. alert → profile 分发

如果当前告警尚无可执行 Profile，则输出受控 unsupported-profile 结果，而不是回退旧式深链。

---

## Phase 4：Memory / CPU Profile 正式接入

Phase 4 在已有 runtime registry 之上，补齐 CPU / Memory 的 Host Agent 真实数据、MCP tools、execution skills 和 runtimes。

### Host Agent 新真实接口

#### Memory

- `GET /api/v1/system/memory-summary`
- `GET /api/v1/process/top-memory?limit=10`

#### CPU

- `GET /api/v1/system/cpu-summary`
- `GET /api/v1/process/top-cpu?limit=10`

### monitor_provider 新增 provider 函数

- `get_memory_summary_data()`
- `list_top_memory_processes_data(limit=10)`
- `get_cpu_summary_data()`
- `list_top_cpu_processes_data(limit=10)`

支持：

- `mock`
- `remote_host`

并统一复用：

- `X-Host-Agent-Token`
- 结构化错误返回
- `source="remote_host"`

### MCP tools 新增

- `get_memory_summary`
- `list_top_memory_processes`
- `get_cpu_summary`
- `list_top_cpu_processes`

### 新 Profile

#### `memory_pressure_profile`

required evidence:

- `memory_summary`
- `top_memory_processes`

reference / conditional evidence:

- `memory_runbook`

#### `cpu_pressure_profile`

required evidence:

- `cpu_summary`
- `top_cpu_processes`

reference / conditional evidence:

- `cpu_runbook`

### 新 Runtime

- `MemoryInvestigationRuntime`
- `CpuInvestigationRuntime`

两者都复用统一接口：

1. `build_initial_tasks`
2. `update_evidence_store`
3. `build_follow_up_tasks`
4. `decide_stop`
5. `build_report`
6. `verify_report`
7. `normalize_result`
8. `summarize_task_result`
9. `summarize_evidence_store`
10. `compute_no_progress_rounds`

### 为什么 Phase 4 不强行加日志 / 工单

第一版 CPU / Memory Runtime 只做：

- 当前状态查询
- 主要压力源定位
- 基础处置建议
- 有边界的报告收口

暂不强行加入：

- 服务日志
- 历史工单
- GC / heap / pprof / 技术栈假设

原因是：

1. 先把 CPU / Memory 的核心事实证据打通
2. 避免在没有对应 Host Agent 或 MCP 工具支撑时过度推断
3. 保证流程可收敛，不再回到旧式 generic 长链

### Patrol Dispatcher 新映射

默认巡检现在的 alert → profile 映射为：

- `HighDiskUsage` / `DiskFull` → `disk_pressure_profile`
- `HighCPUUsage` → `cpu_pressure_profile`
- `HighMemoryUsage` / `MemoryPressure` → `memory_pressure_profile`

这意味着：

- default patrol 只做 alert discovery + profile dispatch
- 深诊断由对应 runtime profile 接管

---

## Phase 4.5：真实证据验收修复与巡检告警源校正

Phase 4.5 不进入 Phase 5 删除 legacy，而是先把 CPU / Memory 的真实字段兼容和默认巡检告警语义校正到位。

### CPU summary 字段兼容

Host Agent 的 CPU 摘要接口在不同版本下可能返回两种字段风格：

1. 旧风格
   - `usage_percent`
   - `cores`
   - `load_1`
   - `load_5`
   - `load_15`
2. 新风格
   - `cpu_percent`
   - `logical_cpu_count`
   - `load_1m`
   - `load_5m`
   - `load_15m`

主项目在 provider 层统一适配成：

- `usage_percent`
- `cores`
- `logical_cpu_count`
- `load_1`
- `load_5`
- `load_15`

这样 CPU runtime 和报告层不需要承担字段兼容成本。

### Alert Provider 三种模式

新增：

- `AIOPS_ALERT_PROVIDER=mock|remote_host|disabled`

三种模式语义如下：

1. `mock`
   - 保留现有 Demo 告警能力
   - 继续返回服务级 mock 告警
2. `remote_host`
   - 不再返回 `data-sync-service` 这类 mock 服务告警
   - 改为基于 Host Agent 实时摘要合成主机级活跃告警
3. `disabled`
   - 默认巡检直接返回“当前未配置活跃告警源”
   - 不进入 Profile 分发

### remote_host 主机级告警合成

`remote_host` 模式下，巡检告警来源于真实 Host Agent 摘要：

- CPU `status=warning/critical` → `HostHighCPUUsage`
- Memory `status=warning/critical` → `HostHighMemoryUsage`
- Disk `status=warning/critical` → `HostHighDiskUsage`

告警字段统一为：

- `alert_name`
- `severity`
- `resource_type`
- `host`
- `description`
- `source="remote_host"`

### Patrol Dispatcher 映射补充

默认巡检额外支持主机级 alert → profile：

- `HostHighCPUUsage` → `cpu_pressure_profile`
- `HostHighMemoryUsage` → `memory_pressure_profile`
- `HostHighDiskUsage` → `disk_pressure_profile`

这样默认巡检在 `remote_host` 模式下，告警对象和后续调查对象都保持主机语义一致。

---

## 当前统一架构

### Diagnosis Profile

当前正式可执行 Profile：

- `patrol_dispatch_profile`
- `disk_pressure_profile`
- `memory_pressure_profile`
- `cpu_pressure_profile`

### Investigation Runtime

当前已注册 runtime：

- `DiskInvestigationRuntime`
- `MemoryInvestigationRuntime`
- `CpuInvestigationRuntime`

### Engine State

统一依赖：

- `selected_profile`
- `evidence_store`
- `investigation_round`
- `no_progress_rounds`
- `stop_decision`

### Legacy Compatibility

仍暂时保留但已标记 legacy：

- 旧 `disk_cleanup` 专项兼容逻辑
- 旧 patrol 深链兼容逻辑
- 旧 generic fallback 兼容逻辑

当前默认路径优先级已经切换到：

- runtime registry
- patrol dispatcher
- execution_profile

---

## Phase 5 规划

后续建议按以下顺序继续推进：

1. 迁移 `memory_profile` / `cpu_profile` 的更多扩展证据
   - 服务日志
   - 历史工单
   - 更细粒度应用指标
2. 继续把更多 alert 映射到结构化 Profile
3. 删除 legacy `disk_cleanup` / patrol 深链兼容分支
4. 最终让 default patrol 完全成为 dispatcher，而不是诊断模板入口

重构完成后的 AIOps 应该统一表现为：

- Planner 负责选 Profile / 出结构化任务
- Executor 只执行任务指定工具
- Replanner 基于 evidence_store 决定补查还是收口
- Verifier 只做证据一致性检查，不再生成自由文本计划
