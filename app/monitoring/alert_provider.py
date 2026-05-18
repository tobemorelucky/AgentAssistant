"""Alert source abstraction for default AIOps patrol."""

from __future__ import annotations

from typing import Any

from app.config import config
from app.monitoring.monitor_provider import (
    get_cpu_summary_data,
    get_disk_usage_data,
    get_memory_summary_data,
)


def get_alert_provider_name() -> str:
    provider = (config.aiops_alert_provider or "mock").strip().lower()
    return provider if provider in {"mock", "remote_host", "disabled"} else "mock"


def _severity_from_status(status: str) -> str:
    normalized = (status or "").strip().lower()
    if normalized == "critical":
        return "critical"
    if normalized == "warning":
        return "high"
    if normalized == "healthy":
        return "info"
    return "medium"


def build_remote_host_alerts() -> dict[str, Any]:
    active_alerts: list[dict[str, Any]] = []

    cpu_summary = get_cpu_summary_data()
    memory_summary = get_memory_summary_data()
    disk_summary = get_disk_usage_data(mount="/")

    for payload, alert_name, resource_type, description in (
        (
            cpu_summary,
            "HostHighCPUUsage",
            "cpu",
            "主机 CPU 压力信号来自 Host Agent 实时摘要。",
        ),
        (
            memory_summary,
            "HostHighMemoryUsage",
            "memory",
            "主机内存压力信号来自 Host Agent 实时摘要。",
        ),
        (
            disk_summary,
            "HostHighDiskUsage",
            "disk",
            "主机磁盘压力信号来自 Host Agent 实时摘要。",
        ),
    ):
        if not isinstance(payload, dict) or payload.get("ok") is False:
            continue
        status = str(payload.get("status") or "").lower()
        if status not in {"warning", "critical"}:
            continue
        host = str(payload.get("host") or "unknown-host")
        active_alerts.append(
            {
                "alert_name": alert_name,
                "severity": _severity_from_status(status),
                "resource_type": resource_type,
                "host": host,
                "service_name": host,
                "description": description,
                "status": "active",
                "source": "remote_host",
            }
        )

    if active_alerts:
        message = "已基于 Host Agent 实时摘要生成主机级活跃告警。"
    else:
        message = "当前未发现达到 warning/critical 的主机级活跃告警。"

    return {
        "active_alerts": active_alerts,
        "total": len(active_alerts),
        "provider": "remote_host",
        "message": message,
    }


def build_disabled_alert_result() -> dict[str, Any]:
    return {
        "active_alerts": [],
        "total": 0,
        "provider": "disabled",
        "message": "当前未配置活跃告警源，未进入 Profile 分发。",
    }
