---
name: Draft session-1778215844721-9r6a8f
description: Auto-generated draft from session session-1778215844721-9r6a8f.
tools:
  - get_current_timestamp
  - retrieve_knowledge
risk_level: low_risk
trigger:
  keywords:
    - 诊断当前系统是否存在告警，如果存在告警请详细分析告警原因并生成诊断报告，诊断报告输出格式要求：
    - ```
    - #
    - 告警分析报告
    - ##
    - 活跃告警清单
  intents:
    - incident_followup
steps:
  - 步骤1: 使用 get_current_timestamp 获取当前时间戳，用于确定告警查询的时间范围
  - 步骤2: 使用 search_topic_by_service_name 搜索与告警相关的日志主题，了解可用的监控数据源
  - 步骤3: 使用 search_log 工具查询最近30分钟内的ERROR级别日志和告警信息，识别活跃告警
  - 步骤4: 对发现的每个告警，使用 query_cpu_metrics 查询相关服务的CPU使用率指标
  - 步骤5: 对发现的每个告警，使用 query_memory_metrics 查询相关服务的内存使用率指标
output_format:
  - Root cause
  - Evidence
  - Risk
  - Recommendation
---

# Draft session-1778215844721-9r6a8f

This draft was generated from a completed diagnosis session. Review before enabling.