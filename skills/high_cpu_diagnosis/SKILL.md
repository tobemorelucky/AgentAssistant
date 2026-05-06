---
name: High CPU Diagnosis
description: Diagnose alerts or incidents related to sustained CPU saturation on a service.
tools:
  - query_cpu_metrics
  - query_process_list
  - search_service_logs
  - search_historical_tickets
risk_level: low_risk
trigger:
  keywords:
    - cpu
    - high cpu
    - 使用率高
    - 飙升
  services:
    - data-sync-service
  alerts:
    - HighCPUUsage
  intents:
    - cpu_diagnosis
steps:
  - Collect recent CPU metric trends and identify spikes, persistence, and saturation windows.
  - Check related process or service behavior around the abnormal period.
  - Search service logs for throttling, backlog, timeout, or retry storms.
  - Compare with historical tickets and summarize likely recurring patterns.
output_format:
  - CPU trend summary
  - Likely hotspots
  - Supporting evidence
  - Risk and remediation suggestions
---

# High CPU Diagnosis

Use this skill when a service shows sustained CPU usage spikes or CPU-related alerts. Focus on proving whether the issue is load-driven, process-driven, or caused by retry storms or runaway jobs.
