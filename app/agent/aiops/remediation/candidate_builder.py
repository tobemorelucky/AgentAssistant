"""Build remediation candidates from finalized diagnosis state."""

from __future__ import annotations

from typing import Any

from app.agent.aiops.remediation.action_registry import list_profile_actions
from app.agent.aiops.remediation.action_schema import RemediationCandidate


PROFILE_REASON_PREFIX = {
    "disk_pressure_profile": "当前磁盘压力已通过实时容量证据确认。",
    "cpu_pressure_profile": "当前 CPU 压力已通过实时摘要与热点进程确认。",
    "memory_pressure_profile": "当前内存压力已通过实时摘要与热点进程确认。",
}


def _candidate_reason(profile_id: str, action_id: str, state: dict[str, Any]) -> str:
    prefix = PROFILE_REASON_PREFIX.get(profile_id, "当前诊断已确认存在资源压力信号。")
    target_alert = state.get("target_alert") or {}
    alert_name = str(target_alert.get("alert_name") or "").strip()
    if alert_name:
        return f"{prefix} 当前最高优先级告警为 {alert_name}，因此建议评估动作 `{action_id}`。"
    return f"{prefix} 建议评估动作 `{action_id}`。"


def _candidate_params(profile_id: str, action_id: str, state: dict[str, Any]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    target_alert = state.get("target_alert") or {}
    service_name = target_alert.get("service_name")
    if service_name and action_id == "restart_service":
        params["service_name"] = service_name
    if profile_id == "disk_pressure_profile":
        params.setdefault("mount", "/")
    return params


def build_remediation_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    profile = state.get("selected_profile") or {}
    profile_id = str(profile.get("profile_id") or "")
    if profile_id not in {
        "disk_pressure_profile",
        "cpu_pressure_profile",
        "memory_pressure_profile",
    }:
        return []

    candidates: list[dict[str, Any]] = []
    for action in list_profile_actions(profile_id):
        candidate = RemediationCandidate(
            action_id=action.action_id,
            title=action.title,
            description=action.description,
            risk_level=action.risk_level,
            dry_run_supported=action.dry_run_supported,
            approval_required=action.approval_required,
            reason=_candidate_reason(profile_id, action.action_id, state),
            expected_benefit=action.expected_benefit,
            safety_note=action.safety_note,
            params=_candidate_params(profile_id, action.action_id, state),
        )
        candidates.append(candidate.model_dump() if hasattr(candidate, "model_dump") else candidate.dict())
    return candidates


def group_remediation_candidates(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = {
        "low_risk": [],
        "dry_run": [],
        "approval_required": [],
        "forbidden": [],
    }
    for candidate in candidates:
        risk_level = str(candidate.get("risk_level") or "")
        if risk_level == "read_only":
            groups["low_risk"].append(candidate)
        elif risk_level == "safe_dry_run":
            groups["dry_run"].append(candidate)
        elif risk_level == "approval_required":
            groups["approval_required"].append(candidate)
        else:
            groups["forbidden"].append(candidate)
    return groups


def render_remediation_candidates(candidates: list[dict[str, Any]]) -> list[str]:
    groups = group_remediation_candidates(candidates)

    def _render_group(title: str, items: list[dict[str, Any]]) -> list[str]:
        lines = [f"### {title}"]
        if not items:
            lines.append("- 暂无候选动作。")
            return lines
        for item in items:
            lines.append(
                f"- `{item.get('action_id')}` / {item.get('title')}：{item.get('reason')} "
                f"(预期收益：{item.get('expected_benefit') or '未提供'}；安全提示：{item.get('safety_note') or '未提供'})"
            )
        return lines

    lines: list[str] = []
    lines.extend(_render_group("可直接给出的低风险建议", groups["low_risk"]))
    lines.extend(_render_group("可 dry-run 的动作", groups["dry_run"]))
    lines.extend(_render_group("需人工确认或审批的动作", groups["approval_required"]))
    lines.extend(_render_group("禁止自动执行的动作", groups["forbidden"]))
    lines.extend(
        [
            "- 本轮未自动执行任何清理、重启、扩容或限流动作。",
            "- dry-run 仅用于影响评估，不执行实际变更。",
            "- execute 必须经过审批；reboot server、删除数据库目录、删除持久化卷永远禁止自动执行。",
        ]
    )
    return lines
