---
name: Draft debug-aiops-001
description: Auto-generated draft from session debug-aiops-001.
tools:
  - get_current_timestamp
  - search_topic_by_service_name
  - get_current_timestamp
  - get_current_timestamp
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
  - 步骤1: 使用 get_current_timestamp 工具获取当前时间戳，用于确定日志和监控数据的查询时间窗口（最近30分钟）。
  - 步骤2: 使用 search_topic_by_service_name 工具搜索与告警相关的日志主题，尝试查找包含'alert'、'alarm'或'warning'关键词的主题。
  - 步骤3: 如果找到相关主题，使用 search_log 工具查询最近30分钟内的告警日志，查询条件包含错误级别或告警关键词。
  - 步骤4: 使用 search_log 工具，基于通用主题或尝试搜索 'system'、'alert' 等关键词查询最近30分钟的日志，以确认是否存在告警记录。
  - 步骤5: 基于已执行的日志查询结果，直接综合现有信息生成告警分析报告。如果之前的日志查询未返回具体告警内容，则在报告中如实说明未检测到活跃告警，并列出已执行的排查步骤作为证据。
output_format:
  - Root cause
  - Evidence
  - Risk
  - Recommendation
---

# Draft debug-aiops-001

This draft was generated from a completed diagnosis session. Review before enabling.