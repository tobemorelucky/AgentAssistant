"""Runtime registry for evidence-driven investigation profiles."""

from __future__ import annotations

from typing import Any, Protocol

from app.agent.aiops.disk_cleanup import normalize_disk_tool_result, summarize_disk_tool_result

from .cpu_engine import (
    CPU_PRESSURE_PROFILE_ID,
    build_cpu_investigation_report,
    build_follow_up_tasks as build_cpu_follow_up_tasks,
    build_initial_cpu_tasks,
    compute_cpu_no_progress_rounds,
    normalize_cpu_tool_result,
    summarize_cpu_evidence_store,
    summarize_cpu_investigation_task,
    summarize_cpu_tool_result,
    update_cpu_evidence_store,
    verify_cpu_investigation_report,
    decide_cpu_stop,
)
from .disk_engine import (
    DISK_PRESSURE_PROFILE_ID,
    build_disk_investigation_report,
    build_follow_up_tasks as build_disk_follow_up_tasks,
    build_initial_disk_tasks,
    compute_no_progress_rounds as compute_disk_no_progress_rounds,
    decide_disk_stop,
    summarize_disk_investigation_task,
    summarize_evidence_store as summarize_disk_evidence_store,
    update_disk_evidence_store,
    verify_disk_investigation_report,
)
from .memory_engine import (
    MEMORY_PRESSURE_PROFILE_ID,
    build_follow_up_tasks as build_memory_follow_up_tasks,
    build_initial_memory_tasks,
    build_memory_investigation_report,
    compute_memory_no_progress_rounds,
    decide_memory_stop,
    normalize_memory_tool_result,
    summarize_memory_evidence_store,
    summarize_memory_investigation_task,
    summarize_memory_tool_result,
    update_memory_evidence_store,
    verify_memory_investigation_report,
)
from .models import StopDecision


class InvestigationRuntime(Protocol):
    """Protocol implemented by profile runtimes."""

    profile_id: str

    def build_initial_tasks(self, state: dict[str, Any]) -> list[dict[str, Any]]: ...

    def update_evidence_store(
        self,
        state: dict[str, Any],
        task: dict[str, Any],
        raw_result: Any,
    ) -> dict[str, dict[str, Any]]: ...

    def build_follow_up_tasks(self, state: dict[str, Any]) -> list[dict[str, Any]]: ...

    def decide_stop(self, state: dict[str, Any]) -> StopDecision: ...

    def build_report(self, state: dict[str, Any]) -> str: ...

    def verify_report(self, state: dict[str, Any]) -> dict[str, Any]: ...

    def normalize_result(self, task: dict[str, Any], raw_result: Any) -> Any: ...

    def summarize_task_result(self, task: dict[str, Any], normalized_result: Any) -> str: ...

    def summarize_evidence_store(self, state: dict[str, Any]) -> str: ...

    def compute_no_progress_rounds(self, state: dict[str, Any]) -> int: ...


class BaseInvestigationRuntime:
    """Default behaviors shared by investigation runtimes."""

    profile_id = "base"

    def build_initial_tasks(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def update_evidence_store(
        self,
        state: dict[str, Any],
        task: dict[str, Any],
        raw_result: Any,
    ) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    def build_follow_up_tasks(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    def decide_stop(self, state: dict[str, Any]) -> StopDecision:
        raise NotImplementedError

    def build_report(self, state: dict[str, Any]) -> str:
        raise NotImplementedError

    def verify_report(self, state: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def normalize_result(self, task: dict[str, Any], raw_result: Any) -> Any:
        return raw_result

    def summarize_task_result(self, task: dict[str, Any], normalized_result: Any) -> str:
        return str(normalized_result)

    def summarize_evidence_store(self, state: dict[str, Any]) -> str:
        evidence_store = dict(state.get("evidence_store") or {})
        parts: list[str] = []
        for slot, payload in evidence_store.items():
            if not isinstance(payload, dict):
                continue
            parts.append(f"{slot}={payload.get('status')}#{payload.get('attempts', 0)}")
        return " | ".join(parts)

    def compute_no_progress_rounds(self, state: dict[str, Any]) -> int:
        return int(state.get("no_progress_rounds") or 0)


class DiskInvestigationRuntime(BaseInvestigationRuntime):
    """Runtime adapter for the disk pressure profile."""

    profile_id = DISK_PRESSURE_PROFILE_ID

    def build_initial_tasks(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return build_initial_disk_tasks()

    def update_evidence_store(
        self,
        state: dict[str, Any],
        task: dict[str, Any],
        raw_result: Any,
    ) -> dict[str, dict[str, Any]]:
        return update_disk_evidence_store(
            dict(state.get("evidence_store") or {}),
            slot=str(task.get("slot") or ""),
            tool_name=str(task.get("tool") or ""),
            raw_result=raw_result,
        )

    def build_follow_up_tasks(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return build_disk_follow_up_tasks(state)

    def decide_stop(self, state: dict[str, Any]) -> StopDecision:
        return decide_disk_stop(state)

    def build_report(self, state: dict[str, Any]) -> str:
        return build_disk_investigation_report(state)

    def verify_report(self, state: dict[str, Any]) -> dict[str, Any]:
        findings, missing_evidence, risk_warnings = verify_disk_investigation_report(state)
        return {
            "passed": not findings,
            "findings": findings,
            "suggested_next_steps": [],
            "missing_evidence": missing_evidence,
            "risk_warnings": risk_warnings,
        }

    def normalize_result(self, task: dict[str, Any], raw_result: Any) -> Any:
        return normalize_disk_tool_result(str(task.get("tool") or ""), raw_result)

    def summarize_task_result(self, task: dict[str, Any], normalized_result: Any) -> str:
        return summarize_disk_tool_result(str(task.get("tool") or ""), normalized_result)

    def summarize_evidence_store(self, state: dict[str, Any]) -> str:
        return summarize_disk_evidence_store(dict(state.get("evidence_store") or {}))

    def compute_no_progress_rounds(self, state: dict[str, Any]) -> int:
        return compute_disk_no_progress_rounds(
            dict(state.get("evidence_store") or {}),
            previous_no_progress_rounds=int(state.get("no_progress_rounds") or 0),
            last_slot=state.get("last_investigation_slot") if isinstance(state.get("last_investigation_slot"), str) else None,
        )

    def summarize_task(self, task: dict[str, Any]) -> str:
        return summarize_disk_investigation_task(task)


class MemoryInvestigationRuntime(BaseInvestigationRuntime):
    """Runtime adapter for the memory pressure profile."""

    profile_id = MEMORY_PRESSURE_PROFILE_ID

    def build_initial_tasks(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return build_initial_memory_tasks()

    def update_evidence_store(
        self,
        state: dict[str, Any],
        task: dict[str, Any],
        raw_result: Any,
    ) -> dict[str, dict[str, Any]]:
        return update_memory_evidence_store(
            dict(state.get("evidence_store") or {}),
            slot=str(task.get("slot") or ""),
            tool_name=str(task.get("tool") or ""),
            raw_result=raw_result,
        )

    def build_follow_up_tasks(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return build_memory_follow_up_tasks(state)

    def decide_stop(self, state: dict[str, Any]) -> StopDecision:
        return decide_memory_stop(state)

    def build_report(self, state: dict[str, Any]) -> str:
        return build_memory_investigation_report(state)

    def verify_report(self, state: dict[str, Any]) -> dict[str, Any]:
        findings, missing_evidence, risk_warnings = verify_memory_investigation_report(state)
        return {
            "passed": not findings,
            "findings": findings,
            "suggested_next_steps": [],
            "missing_evidence": missing_evidence,
            "risk_warnings": risk_warnings,
        }

    def normalize_result(self, task: dict[str, Any], raw_result: Any) -> Any:
        return normalize_memory_tool_result(str(task.get("tool") or ""), raw_result)

    def summarize_task_result(self, task: dict[str, Any], normalized_result: Any) -> str:
        return summarize_memory_tool_result(str(task.get("tool") or ""), normalized_result)

    def summarize_evidence_store(self, state: dict[str, Any]) -> str:
        return summarize_memory_evidence_store(dict(state.get("evidence_store") or {}))

    def compute_no_progress_rounds(self, state: dict[str, Any]) -> int:
        return compute_memory_no_progress_rounds(
            dict(state.get("evidence_store") or {}),
            previous_no_progress_rounds=int(state.get("no_progress_rounds") or 0),
            last_slot=state.get("last_investigation_slot") if isinstance(state.get("last_investigation_slot"), str) else None,
        )

    def summarize_task(self, task: dict[str, Any]) -> str:
        return summarize_memory_investigation_task(task)


class CpuInvestigationRuntime(BaseInvestigationRuntime):
    """Runtime adapter for the CPU pressure profile."""

    profile_id = CPU_PRESSURE_PROFILE_ID

    def build_initial_tasks(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return build_initial_cpu_tasks()

    def update_evidence_store(
        self,
        state: dict[str, Any],
        task: dict[str, Any],
        raw_result: Any,
    ) -> dict[str, dict[str, Any]]:
        return update_cpu_evidence_store(
            dict(state.get("evidence_store") or {}),
            slot=str(task.get("slot") or ""),
            tool_name=str(task.get("tool") or ""),
            raw_result=raw_result,
        )

    def build_follow_up_tasks(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        return build_cpu_follow_up_tasks(state)

    def decide_stop(self, state: dict[str, Any]) -> StopDecision:
        return decide_cpu_stop(state)

    def build_report(self, state: dict[str, Any]) -> str:
        return build_cpu_investigation_report(state)

    def verify_report(self, state: dict[str, Any]) -> dict[str, Any]:
        findings, missing_evidence, risk_warnings = verify_cpu_investigation_report(state)
        return {
            "passed": not findings,
            "findings": findings,
            "suggested_next_steps": [],
            "missing_evidence": missing_evidence,
            "risk_warnings": risk_warnings,
        }

    def normalize_result(self, task: dict[str, Any], raw_result: Any) -> Any:
        return normalize_cpu_tool_result(str(task.get("tool") or ""), raw_result)

    def summarize_task_result(self, task: dict[str, Any], normalized_result: Any) -> str:
        return summarize_cpu_tool_result(str(task.get("tool") or ""), normalized_result)

    def summarize_evidence_store(self, state: dict[str, Any]) -> str:
        return summarize_cpu_evidence_store(dict(state.get("evidence_store") or {}))

    def compute_no_progress_rounds(self, state: dict[str, Any]) -> int:
        return compute_cpu_no_progress_rounds(
            dict(state.get("evidence_store") or {}),
            previous_no_progress_rounds=int(state.get("no_progress_rounds") or 0),
            last_slot=state.get("last_investigation_slot") if isinstance(state.get("last_investigation_slot"), str) else None,
        )

    def summarize_task(self, task: dict[str, Any]) -> str:
        return summarize_cpu_investigation_task(task)


RUNTIME_REGISTRY: dict[str, InvestigationRuntime] = {
    DISK_PRESSURE_PROFILE_ID: DiskInvestigationRuntime(),
    MEMORY_PRESSURE_PROFILE_ID: MemoryInvestigationRuntime(),
    CPU_PRESSURE_PROFILE_ID: CpuInvestigationRuntime(),
}


def get_runtime(profile_id: str | None) -> InvestigationRuntime | None:
    """Return the registered runtime for a profile id."""
    if not profile_id:
        return None
    return RUNTIME_REGISTRY.get(profile_id)


def has_runtime(profile_id: str | None) -> bool:
    """Whether a runtime exists for the given profile id."""
    return get_runtime(profile_id) is not None
