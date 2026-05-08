"""Replanner node for the governed AIOps workflow."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_qwq import ChatQwen
from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.disk_cleanup import build_disk_cleanup_report, is_disk_cleanup_request
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.trace import create_trace_event
from app.agent.aiops.utils import format_tools_description
from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.tools import get_current_time, retrieve_knowledge


class Response(BaseModel):
    """Structured final response."""

    response: str = Field(...)


class Act(BaseModel):
    """Replanner action."""

    action: str = Field(...)
    new_steps: list[str] = Field(default_factory=list)


replanner_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent(
                """
                你是 AIOps Replanner。请根据用户任务、已完成步骤、待执行计划和可用工具，判断下一步应该：
                - continue：继续执行当前剩余步骤
                - replan：补充更合适的新步骤
                - respond：已经有足够证据，可以生成最终报告

                规则：
                - 证据已经充分时优先 respond，不要无意义扩展步骤。
                - 如果 Verifier 已指出缺失证据，只补缺口，不重复已完成的只读采集。
                - new_steps 必须是 Executor 可执行的自然语言步骤。

                可用工具：
                {tools_description}
                """
            ).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)

response_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent(
                """
                你是 AIOps 报告生成器。请根据用户任务和执行历史产出最终 Markdown 报告。

                报告要求：
                - 明确根因判断及其证据
                - 包含影响范围、风险提示、处理建议
                - 不能声称执行了没有执行的操作
                """
            ).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)


async def replanner(state: PlanExecuteState) -> dict[str, Any]:
    """LangGraph node: decide whether to continue, replan or respond."""
    logger.info("=== Replanner ===")
    input_text = state.get("input", "")
    plan = state.get("plan", [])
    past_steps = state.get("past_steps", [])
    session_id = state.get("session_id", "default")
    verifier_result = state.get("verifier_result", {})

    if is_disk_cleanup_request(input_text, state.get("matched_skills", [])):
        if plan:
            return {
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="replanner",
                        status="success",
                        title="Disk cleanup flow continues",
                        result_summary=f"Remaining steps: {len(plan)}",
                    )
                ]
            }
        response = build_disk_cleanup_report(input_text, past_steps)
        return {
            "response": response,
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="replanner",
                    status="success",
                    title="Disk cleanup report drafted",
                    result_summary=response[:280],
                )
            ],
        }

    if verifier_result and not verifier_result.get("passed", True) and plan:
        return {
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="replanner",
                    status="warning",
                    title="Replanner accepted verifier follow-up steps",
                    result_summary=" | ".join(plan[:3]),
                )
            ]
        }

    max_steps = max(1, int(config.aiops_max_steps))
    if len(past_steps) >= max_steps:
        llm = ChatQwen(model=config.rag_model, api_key=config.dashscope_api_key, temperature=0)
        return await _generate_response(state, llm)

    local_tools = [get_current_time, retrieve_knowledge]
    mcp_client = await get_mcp_client_with_retry()
    mcp_tools = await mcp_client.get_tools()
    tools_description = format_tools_description(local_tools + mcp_tools)
    llm = ChatQwen(model=config.rag_model, api_key=config.dashscope_api_key, temperature=0)

    if plan:
        replanner_chain = replanner_prompt | llm.with_structured_output(Act)
        steps_summary = "\n".join(f"步骤: {step}\n结果: {result[:300]}" for step, result in past_steps)
        act = await replanner_chain.ainvoke(
            {
                "messages": [
                    ("user", f"用户任务: {input_text}"),
                    ("user", f"已完成步骤:\n{steps_summary or '无'}"),
                    ("user", f"待执行计划:\n{chr(10).join(plan)}"),
                ],
                "tools_description": tools_description,
            }
        )
        action = act.action if isinstance(act, Act) else act.get("action", "continue")
        new_steps = act.new_steps if isinstance(act, Act) else act.get("new_steps", [])

        if action == "respond":
            return await _generate_response(state, llm)
        if action == "replan" and new_steps:
            return {"plan": new_steps}
        return {
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="replanner",
                    status="success",
                    title="Replanner continue",
                    result_summary=f"Remaining steps: {len(plan)}",
                )
            ]
        }

    return await _generate_response(state, llm)


async def _generate_response(state: PlanExecuteState, llm: ChatQwen) -> dict[str, Any]:
    """Generate a final report."""
    input_text = state.get("input", "")
    past_steps = state.get("past_steps", [])
    session_id = state.get("session_id", "default")
    execution_history = "\n\n".join(f"### 步骤: {step}\n**结果:**\n{result}" for step, result in past_steps)

    response_gen = response_prompt | llm.with_structured_output(Response)
    response_obj = await response_gen.ainvoke(
        {
            "messages": [
                ("user", f"用户任务: {input_text}"),
                ("user", f"执行历史:\n{execution_history or '无'}"),
                ("user", "请输出最终 AIOps 诊断报告。"),
            ]
        }
    )
    final_response = response_obj.response if isinstance(response_obj, Response) else response_obj.get("response", "")
    return {
        "response": final_response,
        "trace_events": [
            create_trace_event(
                session_id=session_id,
                node="replanner",
                status="success",
                title="Final report drafted",
                result_summary=final_response[:280],
            )
        ],
    }
