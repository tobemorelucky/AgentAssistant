"""
Executor 节点：执行单个步骤
基于 LangGraph 官方教程实现
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_qwq import ChatQwen
from loguru import logger

from app.agent.mcp_client import get_mcp_client_with_retry
from app.agent.aiops.tool_policy import check_tool_policy
from app.agent.aiops.trace import create_trace_event, summarize_result
from app.config import config
from app.tools import get_current_time, retrieve_knowledge
from .state import PlanExecuteState
from .utils import invoke_tool


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def executor(state: PlanExecuteState) -> Dict[str, Any]:
    """
    执行节点：执行计划中的下一个步骤
    
    使用 LangGraph 的 ToolNode 自动处理工具调用
    """
    logger.info("=== Executor：执行步骤 ===")

    plan = state.get("plan", [])

    # 如果计划为空，不执行
    if not plan:
        logger.info("计划为空，跳过执行")
        return {}

    session_id = state.get("session_id", "default")
    task = plan[0]
    logger.info(f"当前任务: {task}")

    try:
        # 获取本地工具
        local_tools = [get_current_time, retrieve_knowledge]

        # 获取 MCP 工具
        mcp_client = await get_mcp_client_with_retry()
        mcp_tools = await mcp_client.get_tools()
        logger.info(f"可用工具数量: 本地 {len(local_tools)} + MCP {len(mcp_tools)}")

        all_tools = local_tools + mcp_tools
        tool_map = {
            tool.name if hasattr(tool, "name") else str(tool): tool
            for tool in all_tools
        }

        # 创建 LLM（绑定工具）
        llm = ChatQwen(
            model=config.rag_model,
            api_key=config.dashscope_api_key,
            temperature=0
        )
        llm_with_tools = llm.bind_tools(all_tools)

        # 构建消息（只包含当前步骤，避免原始任务干扰）
        messages = [
            SystemMessage(content="""你是一个能力强大的助手，负责执行具体的任务步骤。

你可以使用各种工具来完成任务。对于每个步骤：
1. 理解步骤的目标
2. 选择合适的工具，如果已经指定了工具，则使用指定的工具
3. 调用工具获取信息
4. 返回执行结果

注意：
- 如果工具调用失败，请说明失败原因
- 不要编造数据，只返回实际获取的信息
- 执行结果要清晰、准确
- 专注于当前步骤，不要考虑其他任务"""),
            HumanMessage(content=f"请执行以下任务: {task}")
        ]

        pending_action = state.get("pending_action")
        approval_status = pending_action.get("status") if isinstance(pending_action, dict) else None
        if pending_action and approval_status in {"approved", "rejected"}:
            logger.info(f"恢复审批动作: {approval_status}")
            llm_response = None
            tool_calls = list(pending_action.get("tool_calls", []))
        else:
            llm_response = await llm_with_tools.ainvoke(messages)
            tool_calls = list(getattr(llm_response, "tool_calls", []) or [])

        logger.info(f"LLM 响应类型: {type(llm_response)}")

        if tool_calls:
            logger.info(f"检测到 {len(tool_calls)} 个工具调用")
            if llm_response is not None:
                messages.append(llm_response)

            if pending_action and approval_status == "rejected":
                result = "人工审批拒绝了危险工具调用，未执行对应操作。"
                return {
                    "plan": plan[1:],
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
                    "plan": plan[1:],
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
                    raise RuntimeError(f"未找到工具: {tool_name}")

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
                "plan": plan[1:],
                "pending_action": None,
                "status": "running",
                "past_steps": [(task, result)],
                "tools_used": tools_used,
                "trace_events": trace_events + [
                    create_trace_event(
                        session_id=session_id,
                        node="executor",
                        status="success",
                        title="Step executed with tool evidence",
                        result_summary=summarize_result(result),
                    )
                ],
            }
        else:
            logger.info("LLM 未调用工具，直接返回结果")
            result = llm_response.content if hasattr(llm_response, 'content') else str(llm_response)
            return {
                "plan": plan[1:],
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

    except Exception as e:
        logger.error(f"执行步骤失败: {e}", exc_info=True)
        return {
            "plan": plan[1:],
            "status": "running",
            "past_steps": [(task, f"执行失败: {str(e)}")],
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="executor",
                    status="error",
                    title="Step execution failed",
                    result_summary=str(e),
                )
            ],
        }
