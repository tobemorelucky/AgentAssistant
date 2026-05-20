"""Executor node for the governed AIOps workflow."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from loguru import logger

from app.agent.aiops.investigation import get_runtime
from app.agent.aiops.patrol import (
    parse_tool_plan_step,
    parse_tool_results_from_history,
    resolve_structured_step_args,
    step_label_from_plan,
    summarize_structured_tool_result,
)
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.tool_policy import check_tool_policy
from app.agent.aiops.tool_registry import get_aiops_local_tools
from app.agent.aiops.trace import create_trace_event, summarize_result
from app.agent.aiops.utils import invoke_tool
from app.agent.mcp_client import get_mcp_client_with_retry


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_result_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_investigation_task(step_payload: Any) -> dict[str, Any] | None:
    if not isinstance(step_payload, dict):
        return None
    required_keys = {"slot", "tool", "args", "required", "reason"}
    if not required_keys.issubset(set(step_payload)):
        return None
    return {
        "slot": str(step_payload.get("slot") or ""),
        "tool": str(step_payload.get("tool") or ""),
        "args": dict(step_payload.get("args") or {}),
        "required": bool(step_payload.get("required", True)),
        "reason": str(step_payload.get("reason") or ""),
    }


async def _execute_investigation_task(
    state: PlanExecuteState,
    *,
    task: dict[str, Any],
    remaining_plan: list[Any],
    tool_map: dict[str, Any],
) -> dict[str, Any]:
    session_id = state.get("session_id", "default")
    tool_name = task["tool"]
    slot = task["slot"]
    args = task["args"]
    task_label = task.get("reason") or f"Collect {slot} with {tool_name}"
    selected_profile = state.get("selected_profile") or {}
    runtime = get_runtime(selected_profile.get("profile_id") if isinstance(selected_profile, dict) else None)
    if runtime is None:
        raw_result = {"error": f"No runtime registered for profile: {selected_profile}", "tool": tool_name, "args": args}
        return {
            "plan": remaining_plan,
            "status": "running",
            "past_steps": [(task_label, _to_result_text(raw_result))],
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="executor",
                    status="error",
                    title="Investigation runtime missing",
                    result_summary=raw_result["error"],
                    metadata={"slot": slot, "execution_mode": "investigation"},
                )
            ],
        }

    decision = check_tool_policy(tool_name)

    if decision["decision"] == "reject":
        raw_result = {"error": decision["reason"], "tool": tool_name, "args": args}
        evidence_store = runtime.update_evidence_store(dict(state), task, raw_result)
        return {
            "plan": remaining_plan,
            "status": "running",
            "past_steps": [(task_label, _to_result_text(raw_result))],
            "evidence_store": evidence_store,
            "last_investigation_slot": slot,
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="tool_call",
                    status="error",
                    title="Investigation tool rejected",
                    tool_name=tool_name,
                    tool_args=args,
                    result_summary=decision["reason"],
                    metadata={"slot": slot, "execution_mode": "investigation"},
                )
            ],
        }

    if decision["decision"] == "approval_required":
        action_id = str(uuid.uuid4())
        action = {
            "action_id": action_id,
            "step": task_label,
            "tool_name": tool_name,
            "tool_args_summary": summarize_result(args),
            "tool_calls": [{"name": tool_name, "args": args}],
            "reason": decision["reason"],
            "status": "pending",
            "created_at": _now_iso(),
        }
        return {
            "pending_action": action,
            "status": "paused",
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="approval",
                    status="pending",
                    title="Dangerous investigation tool requires approval",
                    tool_name=tool_name,
                    tool_args=args,
                    result_summary=decision["reason"],
                    metadata={"slot": slot, "action_id": action_id, "execution_mode": "investigation"},
                )
            ],
        }

    tool = tool_map.get(tool_name)
    started_at = _now_iso()
    started_ts = time.perf_counter()
    status = "success"
    if tool is None:
        raw_result = {"error": f"Tool not found: {tool_name}", "tool": tool_name, "args": args}
        status = "error"
    else:
        try:
            raw_result = await invoke_tool(tool, args)
        except Exception as exc:
            raw_result = {"error": str(exc), "tool": tool_name, "args": args}
            status = "error"
    duration_ms = int((time.perf_counter() - started_ts) * 1000)
    ended_at = _now_iso()
    normalized_result = runtime.normalize_result(task, raw_result)
    evidence_store = runtime.update_evidence_store(dict(state), task, raw_result)
    result_summary = runtime.summarize_task_result(task, normalized_result)
    return {
        "plan": remaining_plan,
        "status": "running",
        "past_steps": [(task_label, _to_result_text(normalized_result))],
        "tools_used": [tool_name],
        "evidence_store": evidence_store,
        "last_investigation_slot": slot,
        "trace_events": [
            create_trace_event(
                session_id=session_id,
                node="tool_call",
                status=status,
                title=f"Executed {tool_name}",
                tool_name=tool_name,
                tool_args=args,
                result_summary=result_summary,
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                metadata={"slot": slot, "level": decision["level"], "execution_mode": "investigation"},
            ),
            create_trace_event(
                session_id=session_id,
                node="executor",
                status=status,
                title="Investigation task executed",
                result_summary=result_summary,
                metadata={"slot": slot},
            ),
        ],
    }


async def _execute_structured_step(
    state: PlanExecuteState,
    *,
    step_index: int,
    step_payload: dict[str, Any],
    tool_map: dict[str, Any],
) -> dict[str, Any]:
    session_id = state.get("session_id", "default")
    remaining_plan = list(state.get("plan", []))[step_index + 1 :]
    structured_step = parse_tool_plan_step(step_payload)
    if structured_step is None:
        return {}

    tool_name = structured_step.tool
    decision = check_tool_policy(tool_name)
    task_label = step_label_from_plan(step_payload)
    resolved_args = resolve_structured_step_args(
        structured_step,
        state=state,
        previous_results=parse_tool_results_from_history(state.get("past_steps", [])),
    )

    if decision["decision"] == "reject":
        result_payload = {"error": decision["reason"], "tool": tool_name, "args": resolved_args}
        return {
            "plan": remaining_plan,
            "status": "running",
            "past_steps": [(task_label, _to_result_text(result_payload))],
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="tool_call",
                    status="error",
                    title="Blocked tool rejected",
                    tool_name=tool_name,
                    tool_args=resolved_args,
                    result_summary=decision["reason"],
                    metadata={"level": decision["level"], "execution_mode": "structured"},
                )
            ],
        }

    if decision["decision"] == "approval_required":
        action_id = str(uuid.uuid4())
        action = {
            "action_id": action_id,
            "step": task_label,
            "tool_name": tool_name,
            "tool_args_summary": summarize_result(resolved_args),
            "tool_calls": [{"name": tool_name, "args": resolved_args}],
            "reason": decision["reason"],
            "status": "pending",
            "created_at": _now_iso(),
        }
        return {
            "pending_action": action,
            "status": "paused",
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="approval",
                    status="pending",
                    title="Dangerous tool approval required",
                    tool_name=tool_name,
                    tool_args=resolved_args,
                    result_summary=decision["reason"],
                    metadata={"action_id": action_id, "execution_mode": "structured"},
                )
            ],
        }

    tool = tool_map.get(tool_name)
    if tool is None:
        result_payload = {"error": f"Tool not found: {tool_name}", "tool": tool_name, "args": resolved_args}
        return {
            "plan": remaining_plan,
            "status": "running",
            "past_steps": [(task_label, _to_result_text(result_payload))],
            "tools_used": [tool_name],
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="tool_call",
                    status="error",
                    title="Structured tool missing",
                    tool_name=tool_name,
                    tool_args=resolved_args,
                    result_summary=result_payload["error"],
                    metadata={"level": decision["level"], "execution_mode": "structured"},
                )
            ],
        }

    started_at = _now_iso()
    started_ts = time.perf_counter()
    status = "success"
    try:
        tool_result = await invoke_tool(tool, resolved_args)
        normalized_result = tool_result
    except Exception as exc:
        normalized_result = {"error": str(exc), "tool": tool_name, "args": resolved_args}
        status = "error"
    duration_ms = int((time.perf_counter() - started_ts) * 1000)
    ended_at = _now_iso()
    result_summary = summarize_structured_tool_result(tool_name, normalized_result)

    return {
        "plan": remaining_plan,
        "status": "running",
        "past_steps": [(task_label, _to_result_text(normalized_result))],
        "tools_used": [tool_name],
        "trace_events": [
            create_trace_event(
                session_id=session_id,
                node="tool_call",
                status=status,
                title=f"Executed {tool_name}",
                tool_name=tool_name,
                tool_args=resolved_args,
                result_summary=result_summary,
                started_at=started_at,
                ended_at=ended_at,
                duration_ms=duration_ms,
                metadata={"level": decision["level"], "execution_mode": "structured"},
            ),
            create_trace_event(
                session_id=session_id,
                node="executor",
                status=status,
                title="Structured plan step executed",
                result_summary=result_summary,
            ),
        ],
    }


async def executor(state: PlanExecuteState) -> dict[str, Any]:
    """LangGraph node: execute the current plan step."""
    logger.info("=== Executor ===")
    plan = list(state.get("plan", []))
    if not plan:
        return {}

    session_id = state.get("session_id", "default")
    current_step = plan[0]
    task_label = current_step if isinstance(current_step, str) else step_label_from_plan(current_step)
    remaining_plan = plan[1:]

    local_tools = get_aiops_local_tools()
    mcp_client = await get_mcp_client_with_retry()
    mcp_tools = await mcp_client.get_tools()
    all_tools = local_tools + mcp_tools
    tool_map = {tool.name if hasattr(tool, "name") else str(tool): tool for tool in all_tools}

    investigation_task = _parse_investigation_task(current_step)
    selected_profile = state.get("selected_profile") or {}
    runtime = get_runtime(selected_profile.get("profile_id") if isinstance(selected_profile, dict) else None)
    if investigation_task is not None and runtime is not None:
        return await _execute_investigation_task(
            state,
            task=investigation_task,
            remaining_plan=remaining_plan,
            tool_map=tool_map,
        )

    structured_step = parse_tool_plan_step(current_step)
    if structured_step is not None:
        return await _execute_structured_step(state, step_index=0, step_payload=current_step, tool_map=tool_map)

    unsupported_result = {
        "error": (
            "Legacy executor path has been removed. "
            "Only investigation runtime tasks and structured tool steps are supported."
        ),
        "step": task_label,
        "plan_source": state.get("plan_source", ""),
    }
    return {
        "plan": remaining_plan,
        "status": "running",
        "past_steps": [(task_label, _to_result_text(unsupported_result))],
        "trace_events": [
            create_trace_event(
                session_id=session_id,
                node="executor",
                status="warning",
                title="Unsupported legacy executor path skipped",
                result_summary=unsupported_result["error"],
                metadata={"plan_source": state.get("plan_source", ""), "step": task_label},
            )
        ],
    }
