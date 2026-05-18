"""Helpers for patrol alert discovery and profile dispatch."""

from __future__ import annotations

from textwrap import dedent
from typing import Any


PATROL_DISPATCH_PROFILE_ID = "patrol_dispatch_profile"
DISK_ALERT_NAMES = {"HighDiskUsage", "DiskFull", "HostHighDiskUsage"}
CPU_ALERT_NAMES = {"HighCPUUsage", "HostHighCPUUsage"}
MEMORY_ALERT_NAMES = {"HighMemoryUsage", "MemoryPressure", "HostHighMemoryUsage"}

SEVERITY_ORDER = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}


def select_target_alert(alerts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the highest-severity active alert."""
    if not alerts:
        return None

    def _score(alert: dict[str, Any]) -> tuple[int, str, str]:
        severity = str(alert.get("severity") or "").lower()
        alert_name = str(alert.get("alert_name") or "")
        target_name = str(alert.get("host") or alert.get("service_name") or "")
        return (SEVERITY_ORDER.get(severity, -1), alert_name, target_name)

    return max(alerts, key=_score)


def resolve_alert_profile_id(alert: dict[str, Any] | None) -> str | None:
    """Map an alert to an executable investigation profile."""
    if not isinstance(alert, dict):
        return None
    alert_name = str(alert.get("alert_name") or "")
    if alert_name in DISK_ALERT_NAMES:
        return "disk_pressure_profile"
    if alert_name in CPU_ALERT_NAMES:
        return "cpu_pressure_profile"
    if alert_name in MEMORY_ALERT_NAMES:
        return "memory_pressure_profile"
    return None


def suggest_future_profile_id(alert: dict[str, Any] | None) -> str | None:
    """Map a known alert to a future profile identifier for roadmap guidance."""
    if not isinstance(alert, dict):
        return None
    return None


def build_no_alert_patrol_report() -> str:
    """Build the patrol report when no active alert is found."""
    return dedent(
        """
        # AIOps 巡检报告

        ## 巡检结论
        - 当前未发现活跃主机级告警。

        ## 说明
        - 当前告警分发器没有发现 warning / critical 的主机级告警信号。
        - 如需进一步确认主机资源状态，请执行主机健康巡检或专项诊断。
        """
    ).strip()


def build_unconfigured_alert_source_report() -> str:
    """Build the patrol report when alert provider is disabled."""
    return dedent(
        """
        # AIOps 巡检报告

        ## 巡检结论
        - 当前未配置活跃告警源。

        ## 说明
        - 本次未进入告警分发链路。
        - 如需启用默认巡检告警发现，请配置 mock 或 remote_host 告警源。
        """
    ).strip()


def build_unsupported_profile_report(alert: dict[str, Any] | None) -> str:
    """Build a controlled result when an alert has no executable profile yet."""
    alert = alert or {}
    alert_name = str(alert.get("alert_name") or "unknown_alert")
    target_name = str(alert.get("host") or alert.get("service_name") or "unknown-target")
    severity = str(alert.get("severity") or "unknown")
    source = str(alert.get("source") or "unknown")
    future_profile = suggest_future_profile_id(alert)

    next_step = (
        f"- 后续建议补充 `{future_profile}` 结构化 Profile，并接入统一 Investigation Engine。"
        if future_profile
        else "- 后续建议为该告警类型补充 execution_profile，并接入统一 Investigation Engine。"
    )

    return dedent(
        f"""
        # AIOps 巡检报告

        ## 已发现活跃告警
        - 对象：`{target_name}`
        - 告警：`{alert_name}`
        - 严重级别：`{severity}`
        - 来源：`{source}`

        ## 当前处理结果
        - 当前已发现主机级告警，但尚未实现对应的结构化 Investigation Profile。
        - 本次没有继续进入深度自主排查链路。

        ## 后续建议
        {next_step}
        """
    ).strip()
