# AIOps Investigation Architecture

## Overview

AgentAssistant 的 AIOps 能力正在从多条割裂的老路径，迁移到统一的 Evidence-Driven Investigation Engine。

当前主线目标是：
- 用 `DiagnosisProfile` 定义场景边界
- 用 `InvestigationRuntime` 管理任务、证据、收口和报告
- 用 `Evidence Store` 驱动报告和 verifier，而不是依赖自由文本计划

## Why The Old Architecture Was Unstable

在重构前，AIOps 长期并存三类不统一路径：
- 磁盘专项的确定性分支
- 默认巡检的模板化深诊断分支
- custom AIOps 的 generic plan + LLM fallback 分支

这些路径会带来几类典型问题：
- Planner 写的步骤和 Executor 实际执行工具不一致
- Verifier 把自由文本 `suggested_next_steps` 回填成新 plan，导致循环
- Skill 既像路由规则，又像执行模板，责任混乱
- 报告常常混入没有被实时证据支撑的推断

## Core Building Blocks

当前统一引擎的核心构件：

### DiagnosisProfile

定义某一诊断场景的结构化边界，包括：
- `profile_id`
- `supported_intents`
- `resource_type`
- `required_evidence_slots`
- `conditional_evidence_slots`
- `reference_evidence_slots`
- `stop_rules`
- `report_schema`

### InvestigationRuntime

每个 executable profile 对应一个 runtime，统一实现：
- `build_initial_tasks`
- `update_evidence_store`
- `build_follow_up_tasks`
- `decide_stop`
- `build_report`
- `verify_report`
- `normalize_result`
- `summarize_task_result`
- `summarize_evidence_store`
- `compute_no_progress_rounds`

### Evidence Store

Evidence Store 记录每个 evidence slot 的：
- `status`
- `source`
- `payload`
- `attempts`
- `quality`
- `error_message`

报告、replanner 和 verifier 都优先围绕 `evidence_store` 工作。

### Stop Controller

Stop 逻辑以 profile 为中心，避免无限补查。典型收口条件：
- required evidence 已满足
- 无进展轮次达到阈值
- 某些 required slot 已达到最大尝试次数
- 总轮次达到上限

## Skill Semantics

Skill 已拆成三种语义：
- `execution_profile`
- `reference_playbook`
- `draft`

当前原则：
- `execution_profile` 才允许进入可执行 Investigation Engine
- `reference_playbook` 只作为知识参考，不直接驱动执行计划
- `draft` 不进入正式执行

## Phase Progress

### Phase 1: 架构止血

目标：
- 停止 custom generic 长链继续扩散
- 给新引擎补上基础模型和状态字段

结果：
- 新增 investigation 基础模型
- 扩展 `PlanExecuteState`
- 老 Skill 默认降级为 `reference_playbook`
- 未命中 `execution_profile` 的 custom AIOps 不再进入旧 generic 深链
- verifier 不再把自由文本 `suggested_next_steps` 直接回填 plan

### Phase 2: Disk Runtime

目标：
- 把磁盘诊断作为第一个正式 profile 迁入新引擎

结果：
- `disk_pressure_profile`
- `DiskInvestigationRuntime`
- InvestigationTask
- Evidence Store
- Follow-up Tasks
- StopController
- Evidence-grounded Report
- Disk-specific Verifier

### Phase 3: Runtime Registry + Patrol Dispatcher

目标：
- 从 disk-specific if/else 过渡到通用 runtime registry
- 把默认巡检从“深诊断模板”缩成“告警发现 + 分发”

结果：
- 新增 runtime registry
- `patrol_dispatch_profile`
- 默认巡检不再自动回退旧 generic 深链
- 告警可以分发到 `disk_pressure_profile` / `cpu_pressure_profile` / `memory_pressure_profile`

### Phase 4: CPU / Memory Runtime

目标：
- 把 CPU / Memory 正式接入统一引擎

结果：
- `memory_pressure_profile`
- `cpu_pressure_profile`
- `MemoryInvestigationRuntime`
- `CpuInvestigationRuntime`
- 对接 Host Agent 的 CPU / Memory 实时接口

当前第一版 CPU / Memory 只覆盖：
- 当前状态
- 热点进程
- 基础建议
- 有限收口

刻意没有强行加入日志 / 工单，是为了先保证：
- 证据真实
- 报告不乱推断
- 工作流可稳定结束

### Phase 4.5: Alert Provider & CPU Compatibility

目标：
- 统一巡检告警来源
- 校正 Host Agent CPU 字段兼容

结果：
- CPU summary 兼容 `cpu_percent / logical_cpu_count / load_1m`
- 新增 `AIOPS_ALERT_PROVIDER=mock|remote_host|disabled`
- remote_host 巡检可合成主机级告警：
  - `HostHighCPUUsage`
  - `HostHighMemoryUsage`
  - `HostHighDiskUsage`

### Phase 4.8A: Host Health Patrol

目标：
- 让默认 AIOps 巡检不再只是“查活跃告警”
- 升级成真正的主机基础健康巡检

结果：
- 新增 `host_health_patrol_profile`
- 新增 `HostHealthPatrolRuntime`
- 默认 `mode=default` 现在优先进入主机健康巡检
- 巡检至少采集：
  - `cpu_summary`
  - `memory_summary`
  - `disk_usage`
  - optional `active_alerts`

当前 Phase 4.8A 的语义是：
- 如果资源 healthy 且没有活跃告警，输出主机健康报告
- 如果某项异常，报告提示建议进入 CPU / Memory / Disk 专项诊断
- 还不会自动跳入专项 Profile

## Current Executable Profiles

当前已可执行的 profile：
- `host_health_patrol_profile`
- `disk_pressure_profile`
- `memory_pressure_profile`
- `cpu_pressure_profile`
- `patrol_dispatch_profile`（内部能力，主要用于告警分发）

## Default Patrol Today

现在默认巡检的职责已经变为：
- 先做主机健康巡检
- 输出 CPU / 内存 / 磁盘 / 活跃告警的整体结论
- 若发现异常，给出进入专项诊断的建议

而不是：
- 只查一遍告警就结束
- 或直接进入模板化深诊断

## Legacy Paths

当前仍保留但已标记 legacy 的逻辑包括：
- old `disk_cleanup` 兼容分支
- old patrol 深诊断模板链
- old generic fallback compatibility

这些逻辑目前主要用于兼容和安全兜底，Phase 5 才考虑删除。

## Phase 4.8B Next

下一阶段建议优先做：
- host health patrol 检测到异常后，自动 dispatch 到对应专项 profile

推荐顺序：
1. CPU / Memory / Disk 异常识别统一化
2. `host_health_patrol_profile` 到专项 profile 的自动跳转策略
3. 异常后共享已有 evidence，避免重复采集
4. 统一“巡检 -> 分发 -> 专项诊断”的 Trace 语义

## Phase 5 Later

只有在下面条件基本满足后，再进入 Phase 5 删除 legacy：
- host health patrol 稳定
- CPU / Memory / Disk runtime 稳定
- 自动 dispatch 稳定
- 旧 generic / old patrol / old disk 兼容路径不再被主流程依赖
