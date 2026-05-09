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
from app.agent.aiops.tool_registry import get_aiops_local_tools
from app.agent.aiops.tool_policy import check_tool_policy
from app.agent.aiops.trace import create_trace_event
from app.agent.aiops.utils import format_tools_description, unwrap_tool_result
from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.core.llm_factory import llm_factory


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
                If local metrics, logs, or tickets are missing, you must ask for local tools rather than web_search.
                If local runbook retrieval is weak and web_search is available, you may add one web_search step
                to fetch official docs or public troubleshooting references as external_reference only.

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


def _extract_report_sections(past_steps: list[tuple[str, str]]) -> tuple[list[str], list[dict[str, str]], list[str]]:
    local_evidence: list[str] = []
    web_references: list[dict[str, str]] = []
    tool_names: list[str] = []

    for step, raw_result in past_steps:
        tool_name = ""
        if "retrieve_knowledge" in step:
            tool_name = "retrieve_knowledge"
        elif "web_search" in step:
            tool_name = "web_search"
        if tool_name:
            tool_names.append(tool_name)

        parsed = unwrap_tool_result(raw_result)
        if tool_name == "retrieve_knowledge":
            if isinstance(parsed, dict):
                content = str(parsed.get("content", "")).strip()
            else:
                content = str(parsed).strip()
            if content:
                local_evidence.append(content.replace("\n", " ")[:280])
        elif tool_name == "web_search" and isinstance(parsed, dict):
            artifacts = parsed.get("artifacts", []) or []
            for artifact in artifacts[:3]:
                if not isinstance(artifact, dict):
                    continue
                metadata = artifact.get("metadata") or {}
                web_references.append(
                    {
                        "title": str(metadata.get("title") or "未提供标题"),
                        "url": str(metadata.get("source") or "未提供链接"),
                        "summary": str(artifact.get("page_content") or parsed.get("content") or "").replace("\n", " ")[:220],
                    }
                )

    return local_evidence, web_references, tool_names


def _build_generic_template_report(state: PlanExecuteState) -> str:
    task = str(state.get("input", "")).strip() or "未提供任务"
    past_steps = list(state.get("past_steps", []))
    local_evidence, web_references, tool_names = _extract_report_sections(past_steps)

    local_lines = local_evidence[:3] or ["- 本地知识库未返回可直接复用的 Runbook 内容。"]
    local_block = "\n".join(
        line if line.startswith("- ") else f"- {line}"
        for line in local_lines
    )

    if web_references:
        web_block = "\n".join(
            [
                f"- 资料标题：{item['title']}\n  链接：{item['url']}\n  摘要：{item['summary']}\n  用途：用于补充公开文档说明，不作为本地故障直接证据。"
                for item in web_references
            ]
        )
    else:
        web_block = "- 本次诊断未使用联网搜索资料。"

    evidence_note = "已完成本地资料检索" if "retrieve_knowledge" in tool_names else "本次未能完成本地资料检索"
    if "web_search" in tool_names:
        evidence_note += "，并补充了公开文档参考"

    return dedent(
        f"""
        # AIOps 诊断报告

        ## 当前结论
        - 当前流程基于已收集的本地资料与可选外部参考，形成了初步排查建议。
        - 由于这是一条通用自定义诊断链路，本次优先给出镜像冲突的定位思路、风险点与人工确认建议。

        ## 本地知识库 Runbook 证据
        {local_block}

        ## 联网搜索补充资料
        {web_block}

        ## 排查建议
        - 优先核对镜像名称、标签、仓库来源、镜像拉取策略以及是否存在同名不同仓库镜像混用。
        - 如果问题发生在部署或构建阶段，建议进一步确认 `docker pull`、`docker compose`、CI 构建缓存与镜像仓库认证配置是否一致。
        - 如果需要处理本地镜像、构建缓存或容器，请先人工确认受影响服务和回滚方式，再执行变更操作。

        ## 风险提示
        - 本次诊断未执行任何镜像删除、覆盖、pull、prune、rm 或其他危险操作。
        - `docker image rm`、`docker system prune`、批量删除镜像标签等操作都应视为人工确认后执行的高风险动作。

        ## 说明
        - {evidence_note}。
        - 如果后续需要更精确结论，建议补充具体报错信息、镜像名称、标签、仓库地址和触发场景。
        """
    ).strip()


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
    plan_source = state.get("plan_source", "")

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
            local_tools = get_aiops_local_tools()
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

        local_tools = get_aiops_local_tools()
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

    if plan_source == "generic_template_fallback":
        if plan:
            return {
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="replanner",
                        status="success",
                        title="Template fallback plan continues",
                        result_summary=f"Remaining steps: {len(plan)}",
                    )
                ]
            }

        response = _build_generic_template_report(state)
        return {
            "response": response,
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="replanner",
                    status="success",
                    title="Template fallback report drafted",
                    result_summary=response[:280],
                )
            ],
        }

    max_steps = max(1, int(config.aiops_max_steps))
    llm = llm_factory.create_qwen_chat_model(
        preferred_model=config.rag_model,
        temperature=0,
        streaming=True,
    )
    if len(past_steps) >= max_steps:
        return await _generate_response(state, llm)

    local_tools = get_aiops_local_tools()
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
