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
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.tool_policy import check_tool_policy
from app.agent.aiops.trace import create_trace_event, summarize_result
from app.agent.aiops.utils import invoke_tool
from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.tools import get_current_time, retrieve_knowledge


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _execute_tool_directly(
    session_id: str,
    task: str,
    tool_name: str,
    tool: Any,
    args: dict[str, Any],
    level: str,
) -> dict[str, Any]:
    started_at = _now_iso()
    started_ts = time.perf_counter()
    tool_result = await invoke_tool(tool, args)
    normalized_result = normalize_disk_tool_result(tool_name, tool_result)
    duration_ms = int((time.perf_counter() - started_ts) * 1000)
    ended_at = _now_iso()
    result_text = json.dumps(normalized_result, ensure_ascii=False, indent=2)
    result_summary = summarize_disk_tool_result(tool_name, normalized_result)
    return {
        "plan": [],
        "status": "running",
        "past_steps": [(task, result_text)],
        "tools_used": [tool_name],
        "trace_events": [
            create_trace_event(
                session_id=session_id,
                node="tool_call",
                status="success",
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
                status="success",
                title="Disk evidence step executed",
                result_summary=result_summary,
            ),
        ],
    }


async def executor(state: PlanExecuteState) -> dict[str, Any]:
    """LangGraph node: execute the current plan step."""
    logger.info("=== Executor ===")
    plan = state.get("plan", [])
    if not plan:
        return {}

    session_id = state.get("session_id", "default")
    task = plan[0]
    remaining_plan = plan[1:]

    local_tools = [get_current_time, retrieve_knowledge]
    mcp_client = await get_mcp_client_with_retry()
    mcp_tools = await mcp_client.get_tools()
    all_tools = local_tools + mcp_tools
    tool_map = {tool.name if hasattr(tool, "name") else str(tool): tool for tool in all_tools}

    disk_tool_name = extract_disk_tool_name(task)
    if disk_tool_name and is_disk_cleanup_request(state.get("input", ""), state.get("matched_skills", [])):
        decision = check_tool_policy(disk_tool_name)
        if decision["decision"] == "reject":
            return {
                "plan": remaining_plan,
                "status": "running",
                "past_steps": [(task, decision["reason"])],
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
            raise RuntimeError(f"Tool not found: {disk_tool_name}")

        direct_result = await _execute_tool_directly(
            session_id=session_id,
            task=task,
            tool_name=disk_tool_name,
            tool=tool,
            args=DISK_TOOL_ARGS.get(disk_tool_name, {}),
            level=decision["level"],
        )
        direct_result["plan"] = remaining_plan
        return direct_result

    llm = ChatQwen(model=config.rag_model, api_key=config.dashscope_api_key, temperature=0)
    llm_with_tools = llm.bind_tools(all_tools)
    messages = [
        SystemMessage(
            content=(
                "你是 AIOps Executor。请针对当前步骤选择合适工具，先调用工具采集证据，再基于结果返回简洁结论。"
                "不要声称执行了删除、重启或破坏性操作，除非工具结果明确表明真的执行了。"
            )
        ),
        HumanMessage(content=f"请执行这个诊断步骤：{task}"),
    ]

    pending_action = state.get("pending_action")
    approval_status = pending_action.get("status") if isinstance(pending_action, dict) else None
    if pending_action and approval_status in {"approved", "rejected"}:
        llm_response = None
        tool_calls = list(pending_action.get("tool_calls", []))
    else:
        llm_response = await llm_with_tools.ainvoke(messages)
        tool_calls = list(getattr(llm_response, "tool_calls", []) or [])

    if tool_calls:
        if llm_response is not None:
            messages.append(llm_response)

        if pending_action and approval_status == "rejected":
            result = "人工审批拒绝了危险工具调用，本步骤未执行高风险操作。"
            return {
                "plan": remaining_plan,
                "pending_action": None,
                "status": "running",
                "past_steps": [(task, result)],
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
                "past_steps": [(task, result)],
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
                "step": task,
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
                raise RuntimeError(f"Tool not found: {tool_name}")

            started_at = _now_iso()
            started_ts = time.perf_counter()
            tool_result = await invoke_tool(tool, args)
            duration_ms = int((time.perf_counter() - started_ts) * 1000)
            ended_at = _now_iso()
            trace_events.append(
                create_trace_event(
                    session_id=session_id,
                    node="tool_call",
                    status="success",
                    title=f"Executed {tool_name}",
                    tool_name=tool_name,
                    tool_args=args,
                    result_summary=summarize_result(tool_result),
                    started_at=started_at,
                    ended_at=ended_at,
                    duration_ms=duration_ms,
                    metadata={"level": decision["level"]},
                )
            )
            tools_used.append(tool_name)
            tool_messages.append(
                ToolMessage(
                    content=summarize_result(tool_result, max_length=800),
                    tool_call_id=tool_call.get("id") or tool_name,
                )
            )

        messages.extend(tool_messages)
        final_response = await llm_with_tools.ainvoke(messages)
        result = final_response.content if hasattr(final_response, "content") else str(final_response)
        return {
            "plan": remaining_plan,
            "pending_action": None,
            "status": "running",
            "past_steps": [(task, result)],
            "tools_used": tools_used,
            "trace_events": trace_events
            + [
                create_trace_event(
                    session_id=session_id,
                    node="executor",
                    status="success",
                    title="Step executed with tool evidence",
                    result_summary=summarize_result(result),
                )
            ],
        }

    result = llm_response.content if hasattr(llm_response, "content") else str(llm_response)
    return {
        "plan": remaining_plan,
        "status": "running",
        "past_steps": [(task, result)],
        "trace_events": [
            create_trace_event(
                session_id=session_id,
                node="executor",
                status="success",
                title="Step executed without tools",
                result_summary=summarize_result(result),
            )
        ],
    }
