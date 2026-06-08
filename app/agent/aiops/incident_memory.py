"""Backward-compatible incident memory exports."""

from app.agent.aiops.memory.incident_memory import (
    append_incident,
    build_incident_record,
    load_recent_incidents,
    search_similar_incidents,
)


def find_similar_incidents(query: str, limit: int = 3):
    return search_similar_incidents(query, top_k=limit)
