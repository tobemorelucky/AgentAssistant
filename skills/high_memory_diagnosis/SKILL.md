---
name: High Memory Diagnosis
description: Diagnose alerts or incidents related to sustained memory pressure or abnormal memory growth.
tools:
  - query_memory_metrics
  - search_service_logs
  - search_historical_tickets
risk_level: low_risk
trigger:
  keywords:
    - memory
    - high memory
    - oom
    - 内存
  services:
    - data-sync-service
  alerts:
    - HighMemoryUsage
  intents:
    - memory_diagnosis
steps:
  - Collect recent memory metric trends and confirm whether memory usage is increasing or oscillating.
  - Search logs for allocation errors, OOM, cache growth, or batch accumulation signals.
  - Compare with historical tickets to identify recurrent leak or backlog patterns.
output_format:
  - Memory trend summary
  - Possible leak or pressure source
  - Supporting evidence
  - Risk and remediation suggestions
---

# High Memory Diagnosis

Use this skill when a service exhibits memory pressure, OOM symptoms, or abnormal memory growth. Focus on evidence of leaks, backlogs, queue accumulation, or cache expansion.
