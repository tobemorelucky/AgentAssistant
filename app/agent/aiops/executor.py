"""Executor node for the governed AIOps workflow."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_qwq import ChatQwen
from loguru import logger

from app.agent.aiops.disk_cleanup import (
    DISK_TOOL_ARGS,
    extract_disk_tool_name,
    is_disk_cleanup_request,
    normalize_disk_tool_result,
    summarize_disk_tool_result,
)
from app.agent.aiops.investigation import (
    is_disk_pressure_profile,
    update_disk_evidence_store,
)
from app.agent.aiops.patrol import (
    parse_tool_plan_step,
    parse_tool_results_from_history,
    resolve_structured_step_args,
    step_label_from_plan,
    summarize_structured_tool_result,
)
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.tool_registry import get_aiops_local_tools
from app.agent.aiops.tool_policy import check_tool_policy
from app.agent.aiops.trace import create_trace_event, summarize_result
from app.agent.aiops.utils import invoke_tool, unwrap_tool_result
from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.core.llm_factory import llm_factory


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_result_text(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _history_snippets(past_steps: list[tuple[str, str]], *, limit: int = 2) -> list[str]:
    snippets: list[str] = []
    for _, raw_result in reversed(past_steps):
        parsed = unwrap_tool_result(raw_result)
        if isinstance(parsed, dict) and parsed.get("content"):
            text = str(parsed.get("content", "")).strip()
        else:
            text = str(parsed).strip()
        if text.startswith("已整理当前证据并准备生成结论与建议。"):
            continue
        if text:
            snippets.append(text.replace("\n", " ")[:180])
        if len(snippets) >= limit:
            break
    return snippets


def _generic_template_tool_name(task_label: str) -> str | None:
    if "retrieve_knowledge" in task_label:
        return "retrieve_knowledge"
    if "web_search" in task_label:
        return "web_search"
    return None


def _build_generic_query(state: PlanExecuteState, tool_name: str) -> str:
    task = str(state.get("input", "")).strip() or "AIOps custom diagnosis"
    if tool_name == "web_search" and "docker" in task.lower():
        return f"{task} Docker official documentation troubleshooting"
    return task


def _build_generic_template_note(task_label: str, state: PlanExecuteState) -> str:
    lower_label = task_label.lower()
    snippets = _history_snippets(list(state.get("past_steps", [])))
    snippet_text = "；".join(snippets) if snippets else "当前步骤之前没有收集到可复用的资料片段。"

    if any(token in lower_label for token in ("镜像", "标签", "仓库", "冲突")):
        return (
            "已基于当前已收集资料整理排查关注点："
            "优先核对镜像名称、标签、仓库来源、镜像拉取策略以及同名不同仓库镜像混用情况。"
            f"参考摘要：{snippet_text} 当前步骤仅做分析整理，未执行任何镜像删除、覆盖、pull、prune 或 rm 操作。"
        )

    return (
        "已整理当前证据并准备生成结论与建议。"
        f"参考摘要：{snippet_text} 当前流程仅进行了资料检索与分析，未执行任何危险操作。"
    )


def _parse_investigation_task(step_payload: Any) -> dict[str, Any] | None:
    if not isinstance(step_payload, dict):
        return None
    if not {"slot", "tool", "args", "required", "reason"} <= set(step_payload):
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
    decision = check_tool_policy(tool_name)

    if decision["decision"] == "reject":
        raw_result = {"error": decision["reason"], "tool": tool_name, "args": args}
        evidence_store = update_disk_evidence_store(
            dict(state.get("evidence_store") or {}),
            slot=slot,
            tool_name=tool_name,
            raw_result=raw_result,
        )
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
    normalized_result = normalize_disk_tool_result(tool_name, raw_result)
    evidence_store = update_disk_evidence_store(
        dict(state.get("evidence_store") or {}),
        slot=slot,
        tool_name=tool_name,
        raw_result=raw_result,
    )
    result_summary = summarize_disk_tool_result(tool_name, normalized_result)
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


async def _execute_generic_template_step(
    state: PlanExecuteState,
    *,
    task_label: str,
    remaining_plan: list[Any],
    tool_map: dict[str, Any],
) -> dict[str, Any]:
    session_id = state.get("session_id", "default")
    tool_name = _generic_template_tool_name(task_label)

    if tool_name is None:
        note = _build_generic_template_note(task_label, state)
        return {
            "plan": remaining_plan,
            "status": "running",
            "past_steps": [(task_label, note)],
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="executor",
                    status="success",
                    title="Template fallback step summarized",
                    result_summary=note[:240],
                    metadata={"execution_mode": "generic_template_fallback"},
                )
            ],
        }

    decision = check_tool_policy(tool_name)
    if decision["decision"] == "reject":
        result_text = decision["reason"]
        return {
            "plan": remaining_plan,
            "status": "running",
            "past_steps": [(task_label, result_text)],
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="tool_call",
                    status="error",
                    title="Template fallback tool rejected",
                    tool_name=tool_name,
                    tool_args={"query": _build_generic_query(state, tool_name)},
                    result_summary=result_text,
                    metadata={"execution_mode": "generic_template_fallback"},
                )
            ],
        }

    tool = tool_map.get(tool_name)
    if tool is None:
        result_payload = {"error": f"Tool not found: {tool_name}", "tool": tool_name}
        return {
            "plan": remaining_plan,
            "status": "running",
            "past_steps": [(task_label, _to_result_text(result_payload))],
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="tool_call",
                    status="error",
                    title="Template fallback tool missing",
                    tool_name=tool_name,
                    result_summary=result_payload["error"],
                    metadata={"execution_mode": "generic_template_fallback"},
                )
            ],
        }

    args = {"query": _build_generic_query(state, tool_name)}
    started_at = _now_iso()
    started_ts = time.perf_counter()
    status = "success"
    try:
        tool_result = await invoke_tool(tool, args)
    except Exception as exc:
        tool_result = {"error": str(exc), "tool": tool_name, "args": args}
        status = "error"
    duration_ms = int((time.perf_counter() - started_ts) * 1000)
    ended_at = _now_iso()
    result_summary = summarize_structured_tool_result(tool_name, tool_result)

    return {
        "plan": remaining_plan,
        "status": "running",
        "past_steps": [(task_label, _to_result_text(tool_result))],
        "tools_used": [tool_name],
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
                metadata={"level": decision["level"], "execution_mode": "generic_template_fallback"},
            ),
            create_trace_event(
                session_id=session_id,
                node="executor",
                status=status,
                title="Template fallback step executed",
                result_summary=result_summary,
            ),
        ],
    }


async def _execute_tool_directly(
    session_id: str,
    task_label: str,
    tool_name: str,
    tool: Any,
    args: dict[str, Any],
    level: str,
) -> dict[str, Any]:
    started_at = _now_iso()
    started_ts = time.perf_counter()
    status = "success"
    try:
        tool_result = await invoke_tool(tool, args)
        normalized_result = normalize_disk_tool_result(tool_name, tool_result)
    except Exception as exc:
        normalized_result = {"error": str(exc), "tool": tool_name, "args": args}
        status = "error"
    duration_ms = int((time.perf_counter() - started_ts) * 1000)
    ended_at = _now_iso()
    result_text = _to_result_text(normalized_result)
    result_summary = summarize_disk_tool_result(tool_name, normalized_result)
    return {
        "plan": [],
        "status": "running",
        "past_steps": [(task_label, result_text)],
        "tools_used": [tool_name],
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
                metadata={"level": level, "execution_mode": "deterministic"},
            ),
            create_trace_event(
                session_id=session_id,
                node="executor",
                status=status,
                title="Deterministic step executed",
                result_summary=result_summary,
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
        normalized_result = normalize_disk_tool_result(tool_name, tool_result)
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
    if state.get("plan_source") == "generic_template_fallback":
        return await _execute_generic_template_step(
            state,
            task_label=task_label,
            remaining_plan=remaining_plan,
            tool_map=tool_map,
        )

    investigation_task = _parse_investigation_task(current_step)
    if investigation_task is not None and is_disk_pressure_profile(state.get("selected_profile")):
        return await _execute_investigation_task(
            state,
            task=investigation_task,
            remaining_plan=remaining_plan,
            tool_map=tool_map,
        )

    structured_step = parse_tool_plan_step(current_step)
    if structured_step is not None:
        return await _execute_structured_step(state, step_index=0, step_payload=current_step, tool_map=tool_map)

    disk_tool_name = extract_disk_tool_name(task_label)
    if disk_tool_name and is_disk_cleanup_request(state.get("input", ""), state.get("matched_skills", [])):
        decision = check_tool_policy(disk_tool_name)
        if decision["decision"] == "reject":
            return {
                "plan": remaining_plan,
                "status": "running",
                "past_steps": [(task_label, decision["reason"])],
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="tool_call",
                        status="error",
                        title="Blocked disk tool rejected",
                        tool_name=disk_tool_name,
                        tool_args=DISK_TOOL_ARGS.get(disk_tool_name, {}),
                        result_summary=decision["reason"],
                    )
                ],
            }

        tool = tool_map.get(disk_tool_name)
        if tool is None:
            return {
                "plan": remaining_plan,
                "status": "running",
                "past_steps": [(task_label, _to_result_text({"error": f"Tool not found: {disk_tool_name}"}))],
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="tool_call",
                        status="error",
                        title="Disk tool not found",
                        tool_name=disk_tool_name,
                        tool_args=DISK_TOOL_ARGS.get(disk_tool_name, {}),
                        result_summary=f"Tool not found: {disk_tool_name}",
                    )
                ],
            }

        direct_result = await _execute_tool_directly(
            session_id=session_id,
            task_label=task_label,
            tool_name=disk_tool_name,
            tool=tool,
            args=DISK_TOOL_ARGS.get(disk_tool_name, {}),
            level=decision["level"],
        )
        direct_result["plan"] = remaining_plan
        return direct_result

    try:
        llm = llm_factory.create_qwen_chat_model(
            preferred_model=config.rag_model,
            temperature=0,
            streaming=True,
        )
        llm_with_tools = llm.bind_tools(all_tools)
        messages = [
            SystemMessage(
                content=(
                    "You are the AIOps Executor. Execute the current step with tools when needed. "
                    "Use tools conservatively and return concrete evidence only."
                )
            ),
            HumanMessage(content=f"Current step: {task_label}\nOriginal task: {state.get('input', '')}"),
        ]

        pending_action = state.get("pending_action")
        approval_status = pending_action.get("status") if isinstance(pending_action, dict) else None
        if pending_action and approval_status in {"approved", "rejected"}:
            llm_response = None
            tool_calls = list(pending_action.get("tool_calls", []))
        else:
            llm_response = await llm_with_tools.ainvoke(messages)
            tool_calls = list(getattr(llm_response, "tool_calls", []) or [])
    except Exception as exc:
        logger.warning(f"Executor tool-calling failed, degrade to deterministic summary: {exc}")
        fallback_result = await _execute_generic_template_step(
            state,
            task_label=task_label,
            remaining_plan=remaining_plan,
            tool_map=tool_map,
        )
        trace_events = list(fallback_result.get("trace_events", []))
        trace_events.append(
            create_trace_event(
                session_id=session_id,
                node="executor",
                status="warning",
                title="Executor degraded to template fallback",
                result_summary=str(exc)[:240],
                metadata={"reason": str(exc)},
            )
        )
        fallback_result["trace_events"] = trace_events
        return fallback_result

    if tool_calls:
        if llm_response is not None:
            messages.append(llm_response)

        if pending_action and approval_status == "rejected":
            result = "危险工具审批被拒绝，本次未执行该操作。"
            return {
                "plan": remaining_plan,
                "pending_action": None,
                "status": "running",
                "past_steps": [(task_label, result)],
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="approval",
                        status="warning",
                        title="Dangerous tool rejected",
                        tool_name=tool_calls[0].get("name"),
                        tool_args=tool_calls[0].get("args", {}),
                        result_summary=result,
                    )
                ],
            }

        decisions = []
        blocked_decisions = []
        dangerous_decisions = []
        for tool_call in tool_calls:
            decision = check_tool_policy(tool_call.get("name", "unknown"))
            decisions.append((tool_call, decision))
            if decision["decision"] == "reject":
                blocked_decisions.append((tool_call, decision))
            elif decision["decision"] == "approval_required":
                dangerous_decisions.append((tool_call, decision))

        if blocked_decisions:
            blocked_tool, blocked_policy = blocked_decisions[0]
            result = blocked_policy["reason"]
            return {
                "plan": remaining_plan,
                "status": "running",
                "past_steps": [(task_label, result)],
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="tool_call",
                        status="error",
                        title="Blocked tool call rejected",
                        tool_name=blocked_tool.get("name"),
                        tool_args=blocked_tool.get("args", {}),
                        result_summary=result,
                    )
                ],
            }

        if dangerous_decisions and approval_status != "approved":
            dangerous_tool, dangerous_policy = dangerous_decisions[0]
            action_id = str(uuid.uuid4())
            action = {
                "action_id": action_id,
                "step": task_label,
                "tool_name": dangerous_tool.get("name", "unknown"),
                "tool_args_summary": summarize_result(dangerous_tool.get("args", {})),
                "tool_calls": tool_calls,
                "reason": dangerous_policy["reason"],
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
                        tool_name=dangerous_tool.get("name"),
                        tool_args=dangerous_tool.get("args", {}),
                        result_summary=dangerous_policy["reason"],
                        metadata={"action_id": action_id},
                    )
                ],
            }

        tool_messages = []
        tools_used = []
        trace_events = []
        for tool_call, decision in decisions:
            tool_name = tool_call.get("name", "unknown")
            args = tool_call.get("args", {})
            tool = tool_map.get(tool_name)
            if tool is None:
                payload = {"error": f"Tool not found: {tool_name}", "tool": tool_name, "args": args}
                tool_result = payload
                status = "error"
            else:
                status = "success"
                try:
                    tool_result = await invoke_tool(tool, args)
                except Exception as exc:
                    tool_result = {"error": str(exc), "tool": tool_name, "args": args}
                    status = "error"

            tools_used.append(tool_name)
            result_summary = summarize_structured_tool_result(tool_name, tool_result)
            trace_events.append(
                create_trace_event(
                    session_id=session_id,
                    node="tool_call",
                    status=status,
                    title=f"Executed {tool_name}",
                    tool_name=tool_name,
                    tool_args=args,
                    result_summary=result_summary,
                    metadata={"level": decision["level"], "execution_mode": "llm_fallback"},
                )
            )
            if llm_response is not None:
                tool_messages.append(
                    ToolMessage(
                        content=json.dumps(tool_result, ensure_ascii=False),
                        tool_call_id=tool_call.get("id", tool_name),
                    )
                )

        if llm_response is not None:
            follow_up = await llm_with_tools.ainvoke(messages + tool_messages)
            result_text = follow_up.content if isinstance(follow_up.content, str) else json.dumps(follow_up.content, ensure_ascii=False)
        else:
            result_text = "\n".join(trace.get("result_summary", "") for trace in trace_events if trace.get("result_summary"))

        return {
            "plan": remaining_plan,
            "status": "running",
            "past_steps": [(task_label, result_text)],
            "tools_used": tools_used,
            "pending_action": None,
            "trace_events": trace_events
            + [
                create_trace_event(
                    session_id=session_id,
                    node="executor",
                    status="success",
                    title="Executor completed fallback step",
                    result_summary=result_text[:240],
                )
            ],
        }

    result_text = ""
    if llm_response is not None:
        content = llm_response.content
        result_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return {
        "plan": remaining_plan,
        "status": "running",
        "past_steps": [(task_label, result_text or "No tool call was required.")],
        "trace_events": [
            create_trace_event(
                session_id=session_id,
                node="executor",
                status="success",
                title="Executor completed without tool call",
                result_summary=(result_text or "No tool call was required.")[:240],
            )
        ],
    }
