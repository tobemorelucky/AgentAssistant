"""Stop-controller helpers for bounded investigation rounds."""

from __future__ import annotations

from .models import DiagnosisProfile, StopDecision, StopDecisionType


def decide_stop_action(
    *,
    profile: DiagnosisProfile | None,
    no_progress_rounds: int,
    hard_limit_reached: bool = False,
    reason: str = "",
    missing_slots: list[str] | None = None,
) -> StopDecision:
    """Return a bounded stop decision for the investigation engine."""
    if hard_limit_reached:
        return StopDecision(
            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
            reason=reason or "Maximum investigation rounds reached.",
            missing_slots=list(missing_slots or []),
        )

    max_no_progress = 1
    if profile is not None:
        max_no_progress = int(profile.stop_rules.get("max_no_progress_rounds", 1))

    if no_progress_rounds > max_no_progress:
        return StopDecision(
            decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
            reason=reason or "No progress across repeated evidence rounds.",
            missing_slots=list(missing_slots or []),
        )

    return StopDecision(decision=StopDecisionType.CONTINUE, missing_slots=list(missing_slots or []))
