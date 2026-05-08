"""Incident memory persistence and retrieval."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from openai import OpenAI

from app.agent.aiops.runtime_store import INCIDENT_DIR
from app.config import config


INCIDENTS_PATH = INCIDENT_DIR / "incidents.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_incidents() -> list[dict[str, Any]]:
    if not INCIDENTS_PATH.exists():
        return []
    incidents: list[dict[str, Any]] = []
    with INCIDENTS_PATH.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            incidents.append(json.loads(line))
    return incidents


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(value * value for value in vector))


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    left_norm = _norm(left)
    right_norm = _norm(right)
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return _dot(left, right) / (left_norm * right_norm)


def _lexical_similarity(query: str, text: str) -> float:
    query_tokens = set(query.lower().split())
    text_tokens = set(text.lower().split())
    if not query_tokens or not text_tokens:
        return 0.0
    return len(query_tokens & text_tokens) / len(query_tokens | text_tokens)


def _embed_text(text: str) -> list[float] | None:
    if not config.get_embedding_api_key():
        return None
    try:
        model = config.get_validated_text_embedding_model()
        client = OpenAI(
            api_key=config.get_embedding_api_key(),
            base_url=config.get_embedding_api_base(),
        )
        response = client.embeddings.create(
            model=model,
            input=text,
            dimensions=config.get_embedding_dimensions(),
            encoding_format="float",
        )
        return response.data[0].embedding
    except Exception as exc:
        logger.warning(f"Incident memory embedding failed: {exc}")
        return None


def append_incident(record: dict[str, Any]) -> None:
    """Persist one incident memory record."""
    INCIDENT_DIR.mkdir(parents=True, exist_ok=True)
    with INCIDENTS_PATH.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_incident_record(state: dict[str, Any]) -> dict[str, Any]:
    """Build an incident record from final state."""
    summary_text = "\n".join(
        [state.get("input", ""), state.get("response", ""), " ".join(state.get("tools_used", []))]
    )
    return {
        "session_id": state.get("session_id", "default"),
        "user_task": state.get("input", ""),
        "matched_skills": [skill.get("name") for skill in state.get("matched_skills", [])],
        "tools_used": state.get("tools_used", []),
        "key_evidence": [
            {"step": step, "summary": str(result)[:240]}
            for step, result in state.get("past_steps", [])[-5:]
        ],
        "root_cause": state.get("response", "")[:400],
        "recommendations": state.get("response", "")[:800],
        "verifier_result": state.get("verifier_result", {}),
        "created_at": _now_iso(),
        "embedding": _embed_text(summary_text),
    }


def find_similar_incidents(query: str, limit: int = 3) -> list[dict[str, Any]]:
    """Return top similar incidents using embeddings when available."""
    incidents = _load_incidents()
    if not incidents:
        return []

    query_embedding = _embed_text(query)
    scored: list[tuple[float, dict[str, Any]]] = []
    for incident in incidents:
        incident_text = "\n".join(
            [
                incident.get("user_task", ""),
                incident.get("root_cause", ""),
                " ".join(incident.get("tools_used", [])),
            ]
        )
        if query_embedding and isinstance(incident.get("embedding"), list):
            score = _cosine_similarity(query_embedding, incident["embedding"])
        else:
            score = _lexical_similarity(query, incident_text)
        scored.append((score, incident))

    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "session_id": incident.get("session_id"),
            "user_task": incident.get("user_task"),
            "matched_skills": incident.get("matched_skills", []),
            "tools_used": incident.get("tools_used", []),
            "root_cause": incident.get("root_cause", "")[:240],
            "score": round(score, 4),
        }
        for score, incident in scored[:limit]
        if score > 0
    ]
