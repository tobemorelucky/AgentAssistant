"""Rule-based skill routing."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from app.agent.aiops.skill_loader import SkillDefinition, load_skills
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.trace import create_trace_event


INTENT_PATTERNS = {
    "cpu_diagnosis": [r"\bcpu\b", "high cpu", "cpu usage", "cpu过高", "CPU告警"],
    "memory_diagnosis": [r"\bmemory\b", r"\boom\b", "high memory", "内存", "OOM"],
    "log_analysis": [r"\blog\b", "日志", "error", "异常日志"],
    "disk_diagnosis": [
        r"\bdisk\b",
        "disk usage",
        "disk full",
        "high disk",
        "no space left",
        "storage",
        "磁盘",
        "硬盘",
        "磁盘满",
        "硬盘满",
        "清理空间",
        "清理缓存",
    ],
}


def infer_intents(input_text: str) -> list[str]:
    """Infer coarse intents from the user task."""
    normalized = (input_text or "").lower()
    matched: list[str] = []
    for intent, patterns in INTENT_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, normalized):
                matched.append(intent)
                break
    return matched


def _contains_any(normalized_text: str, candidates: list[str]) -> bool:
    return any(candidate.lower() in normalized_text for candidate in candidates)


def score_skill(input_text: str, skill: SkillDefinition) -> tuple[int, list[str]]:
    """Score a skill based on trigger match priority."""
    normalized = (input_text or "").lower()
    trigger = skill.trigger or {}
    reasons: list[str] = []
    score = 0

    services = list(trigger.get("services", []) or [])
    if services and _contains_any(normalized, services):
        score += 100
        reasons.append("service")

    alerts = list(trigger.get("alerts", []) or [])
    if alerts and _contains_any(normalized, alerts):
        score += 90
        reasons.append("alert")

    keywords = list(trigger.get("keywords", []) or [])
    keyword_hits = [keyword for keyword in keywords if keyword.lower() in normalized]
    if keyword_hits:
        score += 50 + len(keyword_hits)
        reasons.append("keyword")

    intents = list(trigger.get("intents", []) or [])
    inferred_intents = infer_intents(input_text)
    if intents and any(intent in inferred_intents for intent in intents):
        score += 20
        reasons.append("intent")

    return score, reasons


def match_skills(input_text: str, limit: int = 3) -> list[dict[str, Any]]:
    """Return top matched skills with routing metadata."""
    matches: list[dict[str, Any]] = []
    for skill in load_skills():
        score, reasons = score_skill(input_text, skill)
        if score <= 0:
            continue
        matches.append(
            {
                "name": skill.name,
                "description": skill.description,
                "tools": skill.tools,
                "risk_level": skill.risk_level,
                "steps": skill.steps,
                "output_format": skill.output_format,
                "summary": skill.summary(),
                "score": score,
                "matched_by": reasons,
                "path": skill.path,
            }
        )

    matches.sort(key=lambda item: item["score"], reverse=True)
    return matches[:limit]


async def skill_router(state: PlanExecuteState) -> dict[str, Any]:
    """LangGraph node: route matching skills for the task."""
    input_text = state.get("input", "")
    matched_skills = match_skills(input_text, limit=3)
    logger.info(f"Skill Router matched {len(matched_skills)} skills")

    trace_event = create_trace_event(
        session_id=state.get("session_id", "default"),
        node="skill_router",
        status="success",
        title=f"Matched {len(matched_skills)} skills",
        result_summary=", ".join(skill["name"] for skill in matched_skills) or "No skills matched",
        metadata={
            "matched_skills": [skill["name"] for skill in matched_skills],
        },
    )

    return {
        "matched_skills": matched_skills,
        "trace_events": [trace_event],
    }
