# AIOps Investigation Engine 第一阶段说明

## 为什么当前三条流程不统一

当前 AIOps 同时存在三条并行路径：

1. `disk_cleanup` 专项确定性分支  
   这条链路可控，但能力集中在磁盘诊断，不具备通用扩展性。

2. `default patrol` 结构化巡检分支  
   这条链路已经引入了部分结构化 ToolPlanStep，但仍然与其他路径共享旧状态模型和旧 verifier / replanner 机制。

3. `custom generic` 自定义诊断长链  
   这条路径历史上依赖自由文本 planner、LLM fallback 和 verifier 的自然语言 suggested_next_steps 回填。近期暴露出：
   - Planner 文本与 Executor 实际执行脱节；
   - verifier 打回后可能持续膨胀 trace；
   - 旧 Skill 被误当成可执行诊断模板，导致证据不足时过度推断。

第一阶段不追求“一次性迁移完”，而是先止血，阻断最不可靠的执行链。

## 新 Investigation Engine 的目标

新的 Investigation Engine 以“证据槽位”和“受控停止”为中心，而不是让 Skill 直接驱动任意执行计划。

第一阶段先落地以下基础模型：

- `DiagnosisIntent`
- `DiagnosisProfile`
- `EvidenceRecord`
- `InvestigationTask`
- `StopDecision`

并补充基础模块：

- `app/agent/aiops/investigation/models.py`
- `app/agent/aiops/investigation/profiles.py`
- `app/agent/aiops/investigation/evidence.py`
- `app/agent/aiops/investigation/stop_controller.py`

这些模块的作用是：

- 统一定义诊断 intent；
- 统一描述某类问题需要哪些 evidence slots；
- 让后续 planner / verifier / replanner 围绕 evidence store 运作；
- 为后续“有限轮数、无进展收口、带限制结论 finalize”提供统一接口。

## Skill 的新定位：Profile / Playbook

第一阶段开始，Skill 不再默认被视为可执行模板，而是分为三类：

- `execution_profile`
  - 可映射到 `DiagnosisProfile`
  - 后续才允许驱动真正的结构化调查链路
- `reference_playbook`
  - 仅作为知识参考
  - 不得直接驱动 Planner 生成执行计划
- `draft`
  - 不进入正式执行链路

兼容策略：

- 旧 `SKILL.md` 如果没有 `skill_mode`，默认按 `reference_playbook` 处理；
- 这样可以先保留已有技能资产，但不会再让它们误驱动深度诊断；
- 当前第一阶段只把 `disk_cleanup` 提升为 `execution_profile`。

## 为什么第一阶段先止血而不是直接全迁移

直接全迁移需要同时重写：

- planner 如何从 Skill 生成 InvestigationTask；
- executor 如何围绕 evidence slots 执行；
- verifier 如何按 profile 校验；
- replanner 如何按 no-progress / missing-slot 生成下一轮任务；
- 默认巡检、磁盘专项、自定义诊断三条链路的统一状态机。

如果在已有回归尚未收敛的情况下直接全迁移，风险会更高。  
因此第一阶段策略是：

1. 先把旧 generic 自定义长链关掉；
2. 保留 default patrol / disk_cleanup 现有可运行能力；
3. 把新 Investigation 基础模型和 stop 机制铺好；
4. 第二阶段再逐个 Profile 迁移。

## 第一阶段后的执行策略

- `default patrol`
  - 继续走现有结构化巡检分支
- `disk_cleanup`
  - 继续走现有专项分支
  - 并被标记为 `execution_profile`
- `custom diagnosis`
  - 如果没有命中 `execution_profile`
  - 默认不再进入 legacy generic 深度自主排查长链
  - 而是输出受控结果并停止

同时新增配置：

- `AIOPS_ALLOW_LEGACY_GENERIC_DIAGNOSIS=false`

默认关闭。只有显式打开时，才允许旧 generic 诊断链存在。

## 后续迁移顺序建议

第二阶段优先迁移顺序建议：

1. `memory_diagnosis`
   - 当前最容易出现“旧 Skill 命中后过度推断”的问题
   - 也最能验证 evidence slots 与 stop controller 的价值

2. `cpu_diagnosis`
   - 与默认巡检的 `HighCPUUsage` 证据要求高度重合
   - 适合作为结构化 Profile 的第二个落点

3. 将 `default patrol` 与 `disk_cleanup` 逐步纳入统一 Investigation Engine
   - 让三条路径最终收敛为一套 profile-driven 证据引擎
