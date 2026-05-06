"""Load and cache project-level AGENT profile."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[3]
AGENT_PROFILE_PATH = ROOT_DIR / "AGENT.md"


@lru_cache(maxsize=1)
def load_agent_profile() -> dict:
    """Load AGENT.md content with a safe fallback."""
    if not AGENT_PROFILE_PATH.exists():
        return {
            "exists": False,
            "path": str(AGENT_PROFILE_PATH),
            "content": (
                "AGENT.md not found. Continue with conservative AIOps diagnosis rules: "
                "collect evidence first, avoid unsafe tools, and clearly state uncertainty."
            ),
        }

    content = AGENT_PROFILE_PATH.read_text(encoding="utf-8").strip()
    return {
        "exists": True,
        "path": str(AGENT_PROFILE_PATH),
        "content": content,
    }


def get_agent_profile_prompt() -> str:
    """Return the raw AGENT profile for prompt injection."""
    profile = load_agent_profile()
    return profile["content"]
