"""Evidence store helpers."""

from __future__ import annotations

from collections import Counter

from .models import DiagnosisProfile, EvidenceRecord, EvidenceStatus


def _model_to_dict(model: EvidenceRecord) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def build_evidence_store(profile: DiagnosisProfile | None) -> dict[str, dict]:
    """Create an empty evidence store keyed by slot."""
    if profile is None:
        return {}

    slots = (
        list(profile.required_evidence_slots)
        + list(profile.conditional_evidence_slots)
        + list(profile.reference_evidence_slots)
    )
    unique_slots = []
    for slot in slots:
        if slot not in unique_slots:
            unique_slots.append(slot)
    return {
        slot: _model_to_dict(EvidenceRecord(slot=slot, status=EvidenceStatus.MISSING))
        for slot in unique_slots
    }


def record_evidence_attempt(
    evidence_store: dict[str, dict],
    *,
    slot: str,
    status: EvidenceStatus,
    source: str = "",
    payload: object = None,
    quality: str = "unknown",
    error_message: str = "",
) -> dict[str, dict]:
    """Update one slot after a tool attempt."""
    current = EvidenceRecord(**evidence_store.get(slot, {"slot": slot}))
    current.status = status
    current.source = source
    current.payload = payload
    current.quality = quality
    current.error_message = error_message
    current.attempts += 1
    evidence_store[slot] = _model_to_dict(current)
    return evidence_store


def count_evidence_statuses(evidence_store: dict[str, dict]) -> dict[str, int]:
    """Summarize evidence status counts."""
    counter = Counter()
    for payload in evidence_store.values():
        status = payload.get("status", EvidenceStatus.MISSING)
        counter[str(status)] += 1
    return dict(counter)
