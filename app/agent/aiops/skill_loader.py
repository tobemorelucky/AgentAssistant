"""Skill loading utilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parents[3]
SKILLS_DIR = ROOT_DIR / "skills"
DRAFTS_DIR = SKILLS_DIR / "drafts"


@dataclass
class SkillDefinition:
    """Parsed skill definition."""

    name: str
    description: str
    skill_mode: str = "reference_playbook"
    profile_id: str | None = None
    tools: list[str] = field(default_factory=list)
    risk_level: str = "low_risk"
    trigger: dict[str, list[str]] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    output_format: list[str] = field(default_factory=list)
    body: str = ""
    path: str = ""
    draft: bool = False

    def summary(self) -> str:
        """Compact prompt-friendly summary."""
        tools = ", ".join(self.tools) if self.tools else "none"
        steps = "\n".join(f"- {step}" for step in self.steps) if self.steps else "- None"
        output_lines = (
            "\n".join(f"- {line}" for line in self.output_format) if self.output_format else "- None"
        )
        return (
            f"Skill: {self.name}\n"
            f"Description: {self.description}\n"
            f"Skill mode: {self.skill_mode}\n"
            f"Profile id: {self.profile_id or 'none'}\n"
            f"Risk level: {self.risk_level}\n"
            f"Tools: {tools}\n"
            f"Steps:\n{steps}\n"
            f"Output format:\n{output_lines}"
        )


def _split_front_matter(content: str) -> tuple[dict[str, Any], str]:
    if not content.startswith("---"):
        return {}, content.strip()

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content.strip()

    _, front_matter, body = parts
    data = yaml.safe_load(front_matter) or {}
    return data, body.strip()


def _build_skill(skill_path: Path, draft: bool) -> SkillDefinition:
    raw = skill_path.read_text(encoding="utf-8")
    front_matter, body = _split_front_matter(raw)
    skill_mode = front_matter.get("skill_mode")
    if draft:
        skill_mode = skill_mode or "draft"
    else:
        skill_mode = skill_mode or "reference_playbook"
    return SkillDefinition(
        name=front_matter.get("name", skill_path.parent.name),
        description=front_matter.get("description", ""),
        skill_mode=skill_mode,
        profile_id=front_matter.get("profile_id"),
        tools=list(front_matter.get("tools", []) or []),
        risk_level=front_matter.get("risk_level", "low_risk"),
        trigger=dict(front_matter.get("trigger", {}) or {}),
        steps=list(front_matter.get("steps", []) or []),
        output_format=list(front_matter.get("output_format", []) or []),
        body=body,
        path=str(skill_path),
        draft=draft,
    )


def load_skills(include_drafts: bool = False) -> list[SkillDefinition]:
    """Load all enabled skills, optionally including drafts."""
    skills: list[SkillDefinition] = []
    if SKILLS_DIR.exists():
        for skill_path in SKILLS_DIR.glob("*/SKILL.md"):
            if "drafts" in skill_path.parts:
                continue
            skills.append(_build_skill(skill_path, draft=False))

    if include_drafts and DRAFTS_DIR.exists():
        for skill_path in DRAFTS_DIR.glob("*/SKILL.md"):
            skills.append(_build_skill(skill_path, draft=True))

    return sorted(skills, key=lambda skill: skill.name.lower())


def load_skill_drafts() -> list[SkillDefinition]:
    """Load draft skills only."""
    return [skill for skill in load_skills(include_drafts=True) if skill.draft]
