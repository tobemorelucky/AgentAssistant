# 2026-05-16 Investigation Phase 3

## 变更摘要

- 新增 `investigation/runtime.py`，建立通用 runtime registry。
- 将 `disk_pressure_profile` 接入 `DiskInvestigationRuntime`，统一通过 runtime 接口执行：
  - initial tasks
  - evidence update
  - follow-up tasks
  - stop decision
  - report
  - verifier
- 默认巡检改造为 `patrol_dispatch_profile`：
  - 先发现活跃告警
  - 再选择目标告警
  - 最后分发到可执行 Profile
- 目前仅 `HighDiskUsage / DiskFull` 会分发到 `disk_pressure_profile`
- `HighCPUUsage` 等尚未有 runtime 的告警会输出受控 unsupported-profile 结果，不再回退旧 deep patrol 模板链
- 保留 legacy `disk_cleanup` / legacy patrol 深诊断分支作为兼容层，并标注 Phase 5 删除方向

## 验证

- 磁盘 Profile 通过 runtime registry 后，Phase 2 的磁盘行为不退化
- 默认巡检的默认入口不再继续使用旧 structured patrol 深诊断模板链
- 新增 Phase 3 最小回归测试，覆盖：
  - runtime registry 暴露 disk runtime
  - 默认 profile 变为 `patrol_dispatch_profile`
  - patrol dispatcher 能把 `HighDiskUsage` 映射到 `disk_pressure_profile`
  - 不支持的告警会输出受控说明
