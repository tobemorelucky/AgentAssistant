"""Replanner node for the governed AIOps workflow."""

from __future__ import annotations

from textwrap import dedent

from langchain_core.prompts import ChatPromptTemplate
from langchain_qwq import ChatQwen
from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.disk_cleanup import build_disk_cleanup_report, is_disk_cleanup_request
from app.agent.aiops.patrol import (
    build_alert_report,
    collect_evidence_gaps,
    tool_plan_steps_to_dicts,
)
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.tool_policy import check_tool_policy
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
                You are the AIOps Replanner.
                Decide whether to continue, replan, or respond.
                Only ask for more steps when the execution history clearly lacks evidence.

                Available tools:
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
                You are an AIOps report writer.
                Produce a concise Markdown report grounded in the execution history.
                Never invent evidence that was not returned by tools.
                """
            ).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)


async def replanner(state: PlanExecuteState) -> dict[str, object]:
    """LangGraph node: decide whether to continue, replan or respond."""
    logger.info("=== Replanner ===")
    input_text = state.get("input", "")
    plan = list(state.get("plan", []))
    past_steps = state.get("past_steps", [])
    session_id = state.get("session_id", "default")
    verifier_result = state.get("verifier_result", {})
    target_alert = state.get("target_alert")
    matched_skills = state.get("matched_skills", [])

    if is_disk_cleanup_request(input_text, matched_skills):
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

    if target_alert:
        if verifier_result and not verifier_result.get("passed", True):
            local_tools = [get_current_time, retrieve_knowledge]
            mcp_client = await get_mcp_client_with_retry()
            mcp_tools = await mcp_client.get_tools()
            tool_names = [tool.name if hasattr(tool, "name") else str(tool) for tool in (local_tools + mcp_tools)]
            blocked_tools = {name for name in tool_names if check_tool_policy(name).get("decision") == "reject"}
            evidence_gaps = collect_evidence_gaps(
                target_alert=target_alert,
                matched_skills=matched_skills,
                past_steps=past_steps,
                available_tools=set(tool_names),
                blocked_tools=blocked_tools,
            )
            if evidence_gaps:
                return {
                    "plan": tool_plan_steps_to_dicts(evidence_gaps),
                    "trace_events": [
                        create_trace_event(
                            session_id=session_id,
                            node="replanner",
                            status="warning",
                            title="Verifier triggered evidence补查",
                            result_summary=" | ".join(step.tool for step in evidence_gaps[:4]),
                        )
                    ],
                }

        if plan:
            return {
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="replanner",
                        status="success",
                        title="Structured patrol continues",
                        result_summary=f"Remaining steps: {len(plan)}",
                    )
                ]
            }

        local_tools = [get_current_time, retrieve_knowledge]
        mcp_client = await get_mcp_client_with_retry()
        mcp_tools = await mcp_client.get_tools()
        tool_names = [tool.name if hasattr(tool, "name") else str(tool) for tool in (local_tools + mcp_tools)]
        blocked_tools = {name for name in tool_names if check_tool_policy(name).get("decision") == "reject"}
        max_steps = max(1, int(config.aiops_max_steps))

        evidence_gaps = collect_evidence_gaps(
            target_alert=target_alert,
            matched_skills=matched_skills,
            past_steps=past_steps,
            available_tools=set(tool_names),
            blocked_tools=blocked_tools,
        )
        if evidence_gaps and len(past_steps) < max_steps:
            return {
                "plan": tool_plan_steps_to_dicts(evidence_gaps[: max_steps - len(past_steps)]),
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="replanner",
                        status="warning",
                        title="Replanner requested more evidence",
                        result_summary=" | ".join(step.tool for step in evidence_gaps[:4]),
                    )
                ],
            }

        response = build_alert_report(input_text, target_alert, past_steps)
        return {
            "response": response,
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="replanner",
                    status="success",
                    title="Default patrol report drafted",
                    result_summary=response[:280],
                    metadata={"remaining_gaps": [step.evidence_type for step in evidence_gaps]},
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
                    result_summary=" | ".join(str(step) for step in plan[:3]),
                )
            ]
        }

    max_steps = max(1, int(config.aiops_max_steps))
    llm = ChatQwen(model=config.rag_model, api_key=config.dashscope_api_key, temperature=0)
    if len(past_steps) >= max_steps:
        return await _generate_response(state, llm)

    local_tools = [get_current_time, retrieve_knowledge]
    mcp_client = await get_mcp_client_with_retry()
    mcp_tools = await mcp_client.get_tools()
    tools_description = format_tools_description(local_tools + mcp_tools)

    if plan:
        replanner_chain = replanner_prompt | llm.with_structured_output(Act)
        steps_summary = "\n".join(f"Step: {step}\nResult: {result[:300]}" for step, result in past_steps)
        act = await replanner_chain.ainvoke(
            {
                "messages": [
                    ("user", f"Original task: {input_text}"),
                    ("user", f"Completed steps:\n{steps_summary or '(empty)'}"),
                    ("user", f"Remaining plan:\n{chr(10).join(str(step) for step in plan)}"),
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


async def _generate_response(state: PlanExecuteState, llm: ChatQwen) -> dict[str, object]:
    """Generate a final report."""
    input_text = state.get("input", "")
    past_steps = state.get("past_steps", [])
    session_id = state.get("session_id", "default")
    execution_history = "\n\n".join(f"### Step: {step}\n**Result:**\n{result}" for step, result in past_steps)

    response_gen = response_prompt | llm.with_structured_output(Response)
    response_obj = await response_gen.ainvoke(
        {
            "messages": [
                ("user", f"Task: {input_text}"),
                ("user", f"Execution history:\n{execution_history or '(empty)'}"),
                ("user", "Write the final AIOps Markdown report."),
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
