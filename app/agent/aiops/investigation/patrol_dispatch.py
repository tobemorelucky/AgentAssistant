"""Helpers for default patrol alert discovery and profile dispatch."""

from __future__ import annotations

from textwrap import dedent
from typing import Any


PATROL_DISPATCH_PROFILE_ID = "patrol_dispatch_profile"
DISK_ALERT_NAMES = {"HighDiskUsage", "DiskFull"}
CPU_ALERT_NAMES = {"HighCPUUsage"}

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
        service_name = str(alert.get("service_name") or "")
        return (SEVERITY_ORDER.get(severity, -1), alert_name, service_name)

    return max(alerts, key=_score)


def resolve_alert_profile_id(alert: dict[str, Any] | None) -> str | None:
    """Map an alert to an executable investigation profile."""
    if not isinstance(alert, dict):
        return None
    alert_name = str(alert.get("alert_name") or "")
    if alert_name in DISK_ALERT_NAMES:
        return "disk_pressure_profile"
    return None


def suggest_future_profile_id(alert: dict[str, Any] | None) -> str | None:
    """Map a known alert to a future profile identifier for roadmap guidance."""
    if not isinstance(alert, dict):
        return None
    alert_name = str(alert.get("alert_name") or "")
    if alert_name in CPU_ALERT_NAMES:
        return "cpu_pressure_profile"
    return None


def build_no_alert_patrol_report() -> str:
    """Build the patrol report when no active alert is found."""
    return dedent(
        """
        # AIOps 巡检报告

        ## 巡检结果
        - 当前未检测到活跃告警。

        ## 说明
        - 本次巡检已完成活跃告警发现。
        - 由于没有发现需要深度排查的目标告警，本次未继续进入结构化 Investigation Profile。
        """
    ).strip()


def build_unsupported_profile_report(alert: dict[str, Any] | None) -> str:
    """Build a controlled result when an alert has no executable profile yet."""
    alert = alert or {}
    alert_name = str(alert.get("alert_name") or "unknown_alert")
    service_name = str(alert.get("service_name") or "unknown_service")
    severity = str(alert.get("severity") or "unknown")
    future_profile = suggest_future_profile_id(alert)

    next_step = (
        f"- 建议后续补充 `{future_profile}`，再将该类告警纳入统一 Investigation Engine。"
        if future_profile
        else "- 建议后续为该类告警补充对应 execution_profile，再接入统一 Investigation Engine。"
    )

    return dedent(
        f"""
        # AIOps 巡检报告

        ## 已发现活跃告警
        - 服务：`{service_name}`
        - 告警：`{alert_name}`
        - 严重级别：`{severity}`

        ## 当前处理结果
        - 当前尚未实现该告警类型对应的结构化 Investigation Profile。
        - 本次巡检已停止进入深度自主排查链路。
        - 为避免产生无证据推断，系统没有回退到旧的 patrol 深诊断模板链。

        ## 后续建议
        {next_step}
        """
    ).strip()
