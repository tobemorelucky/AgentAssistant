---
name: cpu_pressure
description: 针对 CPU 使用率过高、CPU 状态查询和基础处置建议的结构化诊断入口。
skill_mode: execution_profile
profile_id: cpu_pressure_profile
tools:
  - get_cpu_summary
  - list_top_cpu_processes
  - retrieve_knowledge
risk_level: low_risk
trigger:
  alerts:
    - HighCPUUsage
  keywords:
    - CPU
    - cpu
    - CPU高
    - CPU占用
    - high cpu
    - cpu usage
  intents:
    - cpu_diagnosis
steps:
  - 采集当前 CPU 摘要与热点 CPU 进程。
  - 识别主要 CPU 压力来源，并补充 Runbook 参考。
  - 在证据边界内给出处置建议和风险提示。
output_format:
  - 任务与对象
  - 已确认事实
  - 当前 CPU 状态
  - 主要 CPU 消耗来源
  - 候选风险 / 待验证解释
  - 证据缺口
  - 处理建议
  - 风险提示
  - Runbook 参考
---

# CPU Pressure Profile

这个 Skill 只负责把请求路由到 `cpu_pressure_profile`，不再回退到旧 generic 长链。
