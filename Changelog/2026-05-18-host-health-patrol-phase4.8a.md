# 2026-05-18 Host Health Patrol Phase 4.8A

## Summary
- 将默认 AIOps 巡检从“只查活跃告警”升级为真正的主机基础健康巡检。
- 新增 `host_health_patrol_profile` 与 `HostHealthPatrolRuntime`。
- 默认 `mode=default` 现在优先进入主机健康巡检，而不是只走 `patrol_dispatch_profile`。

## Changes
- 新增 `app/agent/aiops/investigation/host_health_engine.py`
- 重写 `app/agent/aiops/investigation/profiles.py`
- 重写 `app/agent/aiops/investigation/patrol_dispatch.py`
- 重写 `app/agent/aiops/investigation/runtime.py`
- 重写 `app/agent/aiops/investigation/__init__.py`
- 更新：
  - `tests/agent/test_investigation_phase1.py`
  - `tests/agent/test_investigation_phase3.py`
  - `tests/agent/test_host_health_patrol_phase48.py`
- 更新 README 与架构文档说明

## Behavior
- 默认巡检至少会检查：
  - CPU 实时状态
  - 内存实时状态
  - 磁盘实时状态
  - 可选主机级活跃告警
- 若资源均 healthy 且无告警，报告会明确写“当前未发现明显资源级异常”。
- 若某项异常，当前阶段只给出进入 CPU / Memory / Disk 专项诊断的建议，不自动跳转。
