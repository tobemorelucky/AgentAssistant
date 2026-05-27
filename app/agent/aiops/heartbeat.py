"""Heartbeat patrol manager for lightweight host scans."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.agent.aiops.remediation.candidate_builder import build_remediation_candidates
from app.agent.aiops.runtime_store import runtime_store
from app.config import config
from app.monitoring.alert_provider import build_disabled_alert_result, build_remote_host_alerts, get_alert_provider_name
from app.monitoring.monitor_provider import (
    get_cpu_summary_data,
    get_disk_usage_data,
    get_memory_summary_data,
)
from app.services.aiops_service import DEFAULT_AIOPS_TASK, aiops_service

try:  # pragma: no cover - optional runtime dependency
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
except ModuleNotFoundError:  # pragma: no cover
    AsyncIOScheduler = None  # type: ignore[assignment]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status_value(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return "unknown"
    if payload.get("ok") is False:
        return "unknown"
    return str(payload.get("status") or "unknown").lower()


def _severity_rank(status: str) -> int:
    mapping = {"critical": 3, "warning": 2, "healthy": 1, "unknown": 0}
    return mapping.get((status or "").lower(), 0)


def _alert_payload() -> dict[str, Any]:
    provider = get_alert_provider_name()
    if provider == "remote_host":
        return build_remote_host_alerts()
    if provider == "disabled":
        return build_disabled_alert_result()
    return {
        "active_alerts": [
            {
                "alert_name": "HighCPUUsage",
                "severity": "critical",
                "resource_type": "cpu",
                "host": "demo-server-01",
                "service_name": "data-sync-service",
                "description": "Mock active alert for heartbeat demo.",
                "status": "active",
                "source": "mock",
            }
        ],
        "total": 1,
        "provider": "mock",
        "message": "Mock alert provider returned one active alert.",
    }


def _overall_status(cpu_status: str, memory_status: str, disk_status: str, active_alert_count: int) -> str:
    if active_alert_count > 0:
        return "abnormal"
    max_status = max((cpu_status, memory_status, disk_status), key=_severity_rank)
    return "healthy" if max_status == "healthy" else ("abnormal" if max_status in {"warning", "critical"} else "unknown")


def _summary_record(
    *,
    heartbeat_id: str,
    trigger: str,
    cpu_summary: dict[str, Any],
    memory_summary: dict[str, Any],
    disk_summary: dict[str, Any],
    alerts: dict[str, Any],
    selected_profile: str = "",
    diagnosis_report_summary: str = "",
    remediation_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    host = (
        cpu_summary.get("host")
        or memory_summary.get("host")
        or disk_summary.get("host")
        or (alerts.get("active_alerts") or [{}])[0].get("host")
        or "unknown-host"
    )
    active_alert_count = len(alerts.get("active_alerts") or [])
    cpu_status = _status_value(cpu_summary)
    memory_status = _status_value(memory_summary)
    disk_status = _status_value(disk_summary)
    return {
        "heartbeat_id": heartbeat_id,
        "timestamp": _now_iso(),
        "trigger": trigger,
        "host": host,
        "cpu_status": cpu_status,
        "memory_status": memory_status,
        "disk_status": disk_status,
        "active_alert_count": active_alert_count,
        "overall_status": _overall_status(cpu_status, memory_status, disk_status, active_alert_count),
        "selected_profile": selected_profile,
        "diagnosis_report_summary": diagnosis_report_summary,
        "remediation_candidates": remediation_candidates or [],
    }


class HeartbeatPatrolManager:
    """Manage scheduled and manual heartbeat patrols."""

    def __init__(self) -> None:
        self.scheduler = None
        self._run_lock = asyncio.Semaphore(max(1, int(config.aiops_heartbeat_max_concurrent_runs or 1)))

    def start(self) -> None:
        if not config.aiops_heartbeat_enabled:
            logger.info("AIOps heartbeat disabled by configuration.")
            return
        if AsyncIOScheduler is None:
            logger.warning("AIOps heartbeat requested but APScheduler is not installed.")
            return
        if self.scheduler:
            return
        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(
            self._scheduled_run,
            "interval",
            minutes=max(1, int(config.aiops_heartbeat_interval_minutes or 60)),
            id="aiops-heartbeat",
            replace_existing=True,
            max_instances=max(1, int(config.aiops_heartbeat_max_concurrent_runs or 1)),
        )
        self.scheduler.start()
        logger.info(
            "AIOps heartbeat scheduler started: interval=%s minutes, trigger_deep_diagnosis=%s",
            config.aiops_heartbeat_interval_minutes,
            config.aiops_heartbeat_trigger_deep_diagnosis,
        )

    def stop(self) -> None:
        if self.scheduler:
            self.scheduler.shutdown(wait=False)
            self.scheduler = None
            logger.info("AIOps heartbeat scheduler stopped.")

    async def _scheduled_run(self) -> None:
        await self.run_once(trigger="schedule")

    async def run_once(self, trigger: str = "manual", session_id: str | None = None) -> dict[str, Any]:
        async with self._run_lock:
            heartbeat_id = str(uuid.uuid4())
            cpu_summary = get_cpu_summary_data()
            memory_summary = get_memory_summary_data()
            disk_summary = get_disk_usage_data(mount="/")
            alerts = _alert_payload()
            summary = _summary_record(
                heartbeat_id=heartbeat_id,
                trigger=trigger,
                cpu_summary=cpu_summary,
                memory_summary=memory_summary,
                disk_summary=disk_summary,
                alerts=alerts,
            )
            runtime_store.append_audit_event("heartbeat_summary", summary)
            if summary["overall_status"] == "healthy":
                runtime_store.save_heartbeat_record(summary)
                return summary

            if not config.aiops_heartbeat_trigger_deep_diagnosis:
                runtime_store.save_heartbeat_record(summary)
                return summary

            heartbeat_session_id = session_id or f"heartbeat-{heartbeat_id}"
            diagnosis = await aiops_service.run_diagnosis_once(
                session_id=heartbeat_session_id,
                task=DEFAULT_AIOPS_TASK,
                mode="default",
            )
            final_state = diagnosis.get("state") if isinstance(diagnosis.get("state"), dict) else {}
            candidates = build_remediation_candidates(final_state)
            report = str(final_state.get("response") or "").strip()
            record = {
                **summary,
                "selected_profile": str((final_state.get("selected_profile") or {}).get("profile_id") or ""),
                "diagnosis_report_summary": report[:600] if config.aiops_heartbeat_store_report else report[:220],
                "remediation_candidates": candidates,
            }
            runtime_store.append_audit_event(
                "heartbeat_diagnosis",
                {
                    "heartbeat_id": heartbeat_id,
                    "session_id": heartbeat_session_id,
                    "selected_profile": record["selected_profile"],
                    "overall_status": record["overall_status"],
                },
            )
            runtime_store.save_heartbeat_record(record)
            return record

    def latest(self) -> dict[str, Any]:
        return runtime_store.load_latest_heartbeat()

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        return runtime_store.load_heartbeat_history(limit=limit)


heartbeat_manager = HeartbeatPatrolManager()
