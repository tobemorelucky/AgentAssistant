"""Generate and manage skill drafts."""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agent.aiops.skill_loader import DRAFTS_DIR, SKILLS_DIR, load_skill_drafts


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(value: str) -> str:
    allowed = [char.lower() if char.isalnum() else "-" for char in value]
    slug = "".join(allowed).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "incident-skill"


def generate_skill_draft(record: dict[str, Any]) -> str:
    """Create a deterministic draft skill after a session."""
    matched_skills = record.get("matched_skills") or []
    base_name = matched_skills[0] if matched_skills else record.get("session_id", "incident-skill")
    slug = _slugify(base_name)
    draft_dir = DRAFTS_DIR / slug
    if draft_dir.exists():
        slug = f"{slug}-{record.get('session_id', 'draft')}"
        draft_dir = DRAFTS_DIR / slug
    draft_dir.mkdir(parents=True, exist_ok=True)

    tools = record.get("tools_used", [])[:6]
    trigger_keywords = [keyword for keyword in record.get("user_task", "").split()[:6] if keyword]
    steps = [evidence.get("step", "") for evidence in record.get("key_evidence", []) if evidence.get("step")]
    content = "\n".join(
        [
            "---",
            f"name: Draft {base_name}",
            f"description: Auto-generated draft from session {record.get('session_id', 'unknown')}.",
            "tools:",
            *[f"  - {tool}" for tool in tools],
            "risk_level: low_risk",
            "trigger:",
            "  keywords:",
            *[f"    - {keyword}" for keyword in trigger_keywords],
            "  intents:",
            "    - incident_followup",
            "steps:",
            *[f"  - {step}" for step in steps],
            "output_format:",
            "  - Root cause",
            "  - Evidence",
            "  - Risk",
            "  - Recommendation",
            "---",
            "",
            f"# Draft {base_name}",
            "",
            "This draft was generated from a completed diagnosis session. Review before enabling.",
        ]
    )
    skill_path = draft_dir / "SKILL.md"
    skill_path.write_text(content, encoding="utf-8")
    return str(skill_path)


def list_skill_drafts() -> list[dict[str, str]]:
    """List available skill drafts."""
    drafts = []
    for draft in load_skill_drafts():
        draft_path = Path(draft.path)
        drafts.append(
            {
                "name": draft_path.parent.name,
                "path": str(draft_path),
                "description": draft.description,
                "updated_at": _now_iso(),
                "content": draft_path.read_text(encoding="utf-8"),
            }
        )
    return drafts


def enable_skill_draft(draft_name: str) -> str:
    """Move a draft skill into the active skills directory."""
    source_dir = DRAFTS_DIR / draft_name
    target_dir = SKILLS_DIR / draft_name
    if not source_dir.exists():
        raise FileNotFoundError(f"Skill draft not found: {draft_name}")
    if target_dir.exists():
        raise FileExistsError(f"Skill already exists: {draft_name}")
    shutil.move(str(source_dir), str(target_dir))
    return str(target_dir / "SKILL.md")


def delete_skill_draft(draft_name: str) -> None:
    """Delete a draft skill directory."""
    source_dir = DRAFTS_DIR / draft_name
    if source_dir.exists():
        shutil.rmtree(source_dir)
