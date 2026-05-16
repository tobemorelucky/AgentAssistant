# 2026-05-16 Investigation Phase 2

- 将磁盘诊断主入口迁移到新的 `disk_pressure_profile`，不再默认依赖旧 `disk_cleanup` 确定性分支。
- 新增 `app/agent/aiops/investigation/disk_engine.py`，统一管理磁盘证据槽、结构化 `InvestigationTask`、有限轮次收口规则，以及基于 `evidence_store` 的报告生成。
- Planner 现在会为 `disk_pressure_profile` 生成结构化任务；Executor 只执行任务指定工具，并把结果写回 `evidence_store`。
- Replanner / Verifier 改为围绕证据槽补查和收口，不再把自由文本建议回填成新的磁盘计划。
- 保留旧 `disk_cleanup` 分支作为兼容层，但它不再是磁盘专项的默认主入口。
- 回归验证已覆盖：Profile 注册、Skill Router 命中、结构化任务生成、有限轮次收口、证据缺口报告和 Phase 1 基础设施兼容性。
