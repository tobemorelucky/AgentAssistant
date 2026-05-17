---
name: memory_pressure
description: 针对内存使用率过高、内存状态查询和基础处置建议的结构化诊断入口。
skill_mode: execution_profile
profile_id: memory_pressure_profile
tools:
  - get_memory_summary
  - list_top_memory_processes
  - retrieve_knowledge
risk_level: low_risk
trigger:
  alerts:
    - HighMemoryUsage
    - MemoryPressure
  keywords:
    - 内存
    - 内存满
    - memory
    - oom
    - high memory
  intents:
    - memory_diagnosis
steps:
  - 采集当前内存摘要与热点内存进程。
  - 识别主要内存压力来源，并补充 Runbook 参考。
  - 在证据边界内给出处置建议和风险提示。
output_format:
  - 任务与对象
  - 已确认事实
  - 当前内存状态
  - 主要内存消耗来源
  - 候选风险 / 待验证解释
  - 证据缺口
  - 处理建议
  - 风险提示
  - Runbook 参考
---

# Memory Pressure Profile

这个 Skill 只负责把请求路由到 `memory_pressure_profile`，不再注入旧式专家步骤或技术栈假设。
