"""File-backed incident memory for cross-session AIOps case reuse."""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.agent.aiops.runtime_store import INCIDENT_DIR
from app.config import config


INCIDENTS_PATH = INCIDENT_DIR / "incidents.jsonl"
_WORD_PATTERN = re.compile(r"[A-Za-z0-9_./-]+|[\u4e00-\u9fff]{2,}")
_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]+)"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enabled() -> bool:
    return bool(getattr(config, "aiops_incident_memory_enabled", True))


def _truncate_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...(truncated)"


def _redact_sensitive_text(value: Any) -> str:
    text = str(value or "")
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub(
            lambda match: f"{match.group(1)}=[REDACTED]"
            if match.lastindex and match.lastindex >= 2
            else "[REDACTED]",
            text,
        )
    return text


def _sanitize_scalar(value: Any, limit: int = 300) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    return _truncate_text(_redact_sensitive_text(value), limit)


def _compact_list(items: list[Any], limit: int) -> list[Any]:
    return items[: max(0, limit)]


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _WORD_PATTERN.findall(text or "") if token}


def _overlap_score(left: str, right: str) -> float:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = left_tokens & right_tokens
    union = left_tokens | right_tokens
    return len(overlap) / max(1, len(union))


def _extract_target_context(state: dict[str, Any]) -> tuple[str, str, str]:
    target_alert = state.get("target_alert") or {}
    if isinstance(target_alert, dict):
        asset_name = str(
            target_alert.get("service_name")
            or target_alert.get("asset_name")
            or target_alert.get("resource_name")
            or ""
        ).strip()
        host = str(target_alert.get("host") or "").strip()
        asset_id = str(target_alert.get("asset_id") or target_alert.get("instance") or "").strip()
        if asset_name or host or asset_id:
            return asset_id, asset_name, host

    evidence_store = state.get("evidence_store") or {}
    if isinstance(evidence_store, dict):
        for slot in ("cpu_summary", "memory_summary", "disk_usage"):
            record = evidence_store.get(slot) or {}
            payload = record.get("payload") if isinstance(record, dict) else {}
            if not isinstance(payload, dict):
                continue
            host = str(payload.get("host") or "").strip()
            asset_name = str(payload.get("asset_name") or payload.get("service_name") or "").strip()
            asset_id = str(payload.get("asset_id") or "").strip()
            if asset_name or host or asset_id:
                return asset_id, asset_name, host
    return "", "", ""


def _extract_severity(state: dict[str, Any]) -> str:
    target_alert = state.get("target_alert") or {}
    if isinstance(target_alert, dict) and target_alert.get("severity"):
        return str(target_alert.get("severity"))
    abnormal_findings = state.get("abnormal_findings") or []
    if isinstance(abnormal_findings, list):
        severities = [str(item.get("severity")) for item in abnormal_findings if isinstance(item, dict) and item.get("severity")]
        if severities:
            priority = {"critical": 3, "warning": 2, "healthy": 1}
            severities.sort(key=lambda item: priority.get(item.lower(), 0), reverse=True)
            return severities[0]
    return "unknown"


def _extract_symptom(state: dict[str, Any]) -> str:
    target_alert = state.get("target_alert") or {}
    if isinstance(target_alert, dict):
        symptom = (
            target_alert.get("alert_name")
            or target_alert.get("description")
            or target_alert.get("summary")
        )
        if symptom:
            return _sanitize_scalar(symptom, 300) or ""
    input_text = str(state.get("input") or "").strip()
    return _truncate_text(input_text, 300)


def _collect_tools_used(state: dict[str, Any]) -> list[str]:
    return [
        str(tool)
        for tool in _compact_list([tool for tool in list(state.get("tools_used") or []) if tool], 20)
    ]


def _compact_evidence_summary(evidence_store: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for slot, record in list((evidence_store or {}).items())[:12]:
        if not isinstance(record, dict):
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if not isinstance(payload, dict):
            payload = {}
        item: dict[str, Any] = {
            "status": _sanitize_scalar(record.get("status"), 60),
            "source": _sanitize_scalar(record.get("source"), 80),
            "attempts": record.get("attempts"),
        }
        for key in ("host", "service_name", "usage_percent", "used_gb", "available_gb", "status", "message", "content"):
            if payload.get(key) is not None:
                item[key] = _sanitize_scalar(payload.get(key), 240 if key == "content" else 120)
        if isinstance(payload.get("processes"), list) and payload["processes"]:
            first = payload["processes"][0]
            if isinstance(first, dict):
                item["top_process"] = _sanitize_scalar(first.get("process_name") or first.get("name"), 120)
        if isinstance(payload.get("directories"), list) and payload["directories"]:
            first = payload["directories"][0]
            if isinstance(first, dict):
                item["top_directory"] = _sanitize_scalar(first.get("path"), 180)
        if isinstance(payload.get("files"), list) and payload["files"]:
            first = payload["files"][0]
            if isinstance(first, dict):
                item["top_file"] = _sanitize_scalar(first.get("path") or first.get("file"), 180)
        if isinstance(payload.get("artifacts"), list) and payload["artifacts"]:
            artifacts = []
            for artifact in payload["artifacts"][:3]:
                if isinstance(artifact, dict):
                    artifacts.append(
                        {
                            "title": _sanitize_scalar((artifact.get("metadata") or {}).get("title"), 120),
                            "source": _sanitize_scalar((artifact.get("metadata") or {}).get("source"), 240),
                        }
                    )
            if artifacts:
                item["artifacts"] = artifacts
        summary[slot] = item
    return summary


def _extract_abnormal_metrics(evidence_store: dict[str, Any]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for slot, record in list((evidence_store or {}).items())[:12]:
        if not isinstance(record, dict):
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if not isinstance(payload, dict):
            continue
        entry: dict[str, Any] = {"slot": slot}
        for key in ("usage_percent", "used_gb", "available_gb", "images_gb", "volumes_gb", "build_cache_gb"):
            if payload.get(key) is not None:
                entry[key] = payload.get(key)
        if len(entry) > 1:
            metrics.append(entry)
    return metrics[:8]


def _extract_root_cause_summary(state: dict[str, Any]) -> str:
    report = str(state.get("response") or "").strip()
    if not report:
        return ""
    lines = [line.strip(" -#") for line in report.splitlines() if line.strip()]
    interesting = []
    for line in lines:
        if any(token in line for token in ("根因", "结论", "热点", "主要", "压力", "异常")):
            interesting.append(line)
        if len(interesting) >= 3:
            break
    summary = " ".join(interesting) if interesting else " ".join(lines[:3])
    return _sanitize_scalar(summary, 500) or ""


def _extract_report_summary(state: dict[str, Any]) -> str:
    report = str(state.get("response") or "").strip()
    if not report:
        return ""
    lines = [line.strip(" -#") for line in report.splitlines() if line.strip()]
    return _sanitize_scalar(" ".join(lines[:6]), 1000) or ""


def _compact_remediation_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for item in list(candidates or [])[:8]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "action_id": _sanitize_scalar(item.get("action_id"), 120),
                "title": _sanitize_scalar(item.get("title"), 160),
                "risk_level": _sanitize_scalar(item.get("risk_level"), 60),
                "approval_required": bool(item.get("approval_required")),
                "reason": _sanitize_scalar(item.get("reason"), 240),
                "expected_benefit": _sanitize_scalar(item.get("expected_benefit"), 240),
            }
        )
    return compact


def _normalized_profile_id(profile_value: Any) -> str:
    if isinstance(profile_value, dict):
        return str(profile_value.get("profile_id") or "").strip()
    return str(profile_value or "").strip()


def _current_profile_id(state: dict[str, Any]) -> str:
    profile_id = _normalized_profile_id(state.get("selected_profile"))
    if profile_id:
        return profile_id
    matched_skills = state.get("matched_skills") or []
    if isinstance(matched_skills, list):
        for skill in matched_skills:
            if isinstance(skill, dict) and skill.get("profile_id"):
                return str(skill.get("profile_id")).strip()
    return ""


def _current_user_feedback(state: dict[str, Any]) -> str:
    feedback = state.get("feedback") or {}
    if isinstance(feedback, dict):
        comment = str(feedback.get("comment") or "").strip()
        if comment:
            return _sanitize_scalar(comment, 300) or ""
        if feedback.get("helpful") is True:
            return "helpful"
        if feedback.get("helpful") is False and feedback:
            return "not_helpful"
    return ""


def _record_text(incident: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(incident.get("symptom") or ""),
            str(incident.get("user_task") or ""),
            str(incident.get("root_cause_summary") or ""),
            str(incident.get("final_report_summary") or ""),
        ]
    )


def _matched_keyword_reason(query: str, incident: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    query_tokens = _tokenize(query)
    symptom_tokens = _tokenize(str(incident.get("symptom") or ""))
    overlap = query_tokens & symptom_tokens
    if overlap:
        score += min(3.0, 0.6 * len(overlap))
        reasons.append(f"symptom keywords: {', '.join(sorted(overlap)[:4])}")
    return score, reasons


def _ensure_dir() -> None:
    INCIDENT_DIR.mkdir(parents=True, exist_ok=True)


def _load_incidents() -> list[dict[str, Any]]:
    if not _enabled():
        return []
    if not INCIDENTS_PATH.exists():
        return []
    incidents: list[dict[str, Any]] = []
    with INCIDENTS_PATH.open("r", encoding="utf-8") as file:
        for line_number, raw_line in enumerate(file, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception as exc:
                logger.warning(f"Incident memory skipped malformed line {line_number}: {exc}")
                continue
            if isinstance(payload, dict):
                incidents.append(payload)
    return incidents


def load_recent_incidents(limit: int = 20) -> list[dict[str, Any]]:
    incidents = _load_incidents()
    sanitized: list[dict[str, Any]] = []
    for incident in incidents[-max(1, limit) :]:
        sanitized.append(
            {
                "incident_id": _sanitize_scalar(incident.get("incident_id"), 80),
                "session_id": _sanitize_scalar(incident.get("session_id"), 120),
                "created_at": _sanitize_scalar(incident.get("created_at"), 80),
                "asset_name": _sanitize_scalar(incident.get("asset_name"), 120),
                "host": _sanitize_scalar(incident.get("host"), 120),
                "profile_id": _sanitize_scalar(incident.get("profile_id"), 120),
                "symptom": _sanitize_scalar(incident.get("symptom"), 240),
                "severity": _sanitize_scalar(incident.get("severity"), 40),
                "status": _sanitize_scalar(incident.get("status"), 40),
                "verifier_passed": incident.get("verifier_passed"),
                "final_report_summary": _sanitize_scalar(incident.get("final_report_summary"), 1000),
            }
        )
    return list(reversed(sanitized))


def append_incident(record: dict[str, Any]) -> None:
    """Append one incident memory record without blocking the diagnosis flow."""
    if not _enabled():
        return
    if not isinstance(record, dict) or not record:
        return
    _ensure_dir()
    with INCIDENTS_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_incident_record(state: dict[str, Any], *, status: str = "completed") -> dict[str, Any]:
    """Build a compact incident case from final diagnosis state."""
    session_id = str(state.get("session_id") or "default").strip()
    user_task = _sanitize_scalar(state.get("input"), 500) or ""
    profile_id = _current_profile_id(state)
    evidence_store = dict(state.get("evidence_store") or {})
    tools_used = _collect_tools_used(state)
    report_summary = _extract_report_summary(state)
    if not session_id or not user_task or not profile_id:
        return {}
    if not report_summary and not str(state.get("response") or "").strip():
        return {}
    if not evidence_store and not tools_used:
        return {}

    asset_id, asset_name, host = _extract_target_context(state)
    target_alert = state.get("target_alert") or {}
    if isinstance(target_alert, dict) and target_alert.get("service_name") and not asset_name:
        asset_name = str(target_alert.get("service_name"))
    if isinstance(target_alert, dict) and target_alert.get("host") and not host:
        host = str(target_alert.get("host"))

    root_cause_summary = _extract_root_cause_summary(state) or report_summary
    verifier_result = state.get("verifier_result") or {}
    if not isinstance(verifier_result, dict):
        verifier_result = {}

    record = {
        "incident_id": str(uuid.uuid4()),
        "session_id": session_id,
        "created_at": _now_iso(),
        "asset_id": _sanitize_scalar(asset_id, 120) or "",
        "asset_name": _sanitize_scalar(asset_name, 120) or "",
        "host": _sanitize_scalar(host, 120) or "",
        "profile_id": profile_id,
        "symptom": _extract_symptom(state),
        "severity": _extract_severity(state),
        "user_task": user_task,
        "abnormal_metrics": _extract_abnormal_metrics(evidence_store),
        "tools_used": tools_used,
        "evidence_summary": _compact_evidence_summary(evidence_store),
        "root_cause_summary": root_cause_summary,
        "final_report_summary": report_summary,
        "remediation_candidates": _compact_remediation_candidates(state.get("remediation_candidates") or []),
        "verifier_passed": verifier_result.get("passed"),
        "resolved": bool((state.get("feedback") or {}).get("helpful")),
        "user_feedback": _current_user_feedback(state),
        "status": status,
    }
    if status == "failed":
        record["error_summary"] = _sanitize_scalar(
            state.get("error_summary") or state.get("response") or "unknown error",
            500,
        )
    return record


def search_similar_incidents(
    query: str,
    profile_id: str | None = None,
    asset_name: str | None = None,
    host: str | None = None,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Return top-k similar incidents using lightweight rule-based scoring."""
    if not _enabled():
        return []

    query_text = str(query or "").strip()
    incidents = _load_incidents()
    if not query_text or not incidents:
        return []

    normalized_profile_id = str(profile_id or "").strip()
    normalized_asset_name = str(asset_name or "").strip().lower()
    normalized_host = str(host or "").strip().lower()
    scored: list[dict[str, Any]] = []

    for incident in incidents:
        score = 0.0
        matched_reasons: list[str] = []

        incident_profile_id = str(incident.get("profile_id") or "").strip()
        if normalized_profile_id and incident_profile_id == normalized_profile_id:
            score += 4.0
            matched_reasons.append("same profile_id")

        incident_host = str(incident.get("host") or "").strip().lower()
        if normalized_host and incident_host and incident_host == normalized_host:
            score += 2.5
            matched_reasons.append("same host")

        incident_asset_name = str(incident.get("asset_name") or "").strip().lower()
        if normalized_asset_name and incident_asset_name and incident_asset_name == normalized_asset_name:
            score += 2.0
            matched_reasons.append("same asset_name")

        keyword_score, keyword_reasons = _matched_keyword_reason(query_text, incident)
        score += keyword_score
        matched_reasons.extend(keyword_reasons)

        lexical_score = _overlap_score(query_text, _record_text(incident))
        if lexical_score > 0:
            score += lexical_score * 6.0
            matched_reasons.append(f"text overlap={lexical_score:.2f}")

        if incident.get("verifier_passed") is True:
            score += 1.0
            matched_reasons.append("verifier passed")

        status = str(incident.get("status") or "").strip().lower()
        if status == "completed":
            score += 1.0
            matched_reasons.append("completed incident")
        elif status == "failed":
            score -= 1.5
            matched_reasons.append("failed incident downweighted")

        if score <= 0:
            continue

        scored.append(
            {
                "incident_id": incident.get("incident_id"),
                "created_at": incident.get("created_at"),
                "session_id": incident.get("session_id"),
                "asset_name": incident.get("asset_name"),
                "host": incident.get("host"),
                "profile_id": incident.get("profile_id"),
                "symptom": incident.get("symptom"),
                "severity": incident.get("severity"),
                "abnormal_metrics": incident.get("abnormal_metrics", []),
                "tools_used": incident.get("tools_used", []),
                "evidence_summary": incident.get("evidence_summary", {}),
                "root_cause_summary": incident.get("root_cause_summary", ""),
                "final_report_summary": incident.get("final_report_summary", ""),
                "remediation_candidates": incident.get("remediation_candidates", []),
                "verifier_passed": incident.get("verifier_passed"),
                "resolved": incident.get("resolved"),
                "status": incident.get("status"),
                "score": round(score, 4),
                "matched_reasons": matched_reasons[:8],
            }
        )

    scored.sort(
        key=lambda item: (
            item.get("score", 0),
            item.get("verifier_passed") is True,
            item.get("status") == "completed",
            item.get("created_at", ""),
        ),
        reverse=True,
    )
    return scored[: max(1, int(top_k or 3))]
