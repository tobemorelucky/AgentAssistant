"""Replanner node for the governed AIOps workflow."""

from __future__ import annotations

from textwrap import dedent

from langchain_core.prompts import ChatPromptTemplate
from langchain_qwq import ChatQwen
from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.disk_cleanup import (
    build_disk_cleanup_report,
    extract_disk_tools_from_steps,
    is_disk_cleanup_request,
)
from app.agent.aiops.investigation import StopDecision, StopDecisionType, get_runtime
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


LEGACY_GENERIC_PLAN_SOURCES = {
    "generic_llm",
    "generic_template_fallback",
    "controlled_no_profile",
    "legacy_generic_disabled",
}


def _model_to_dict(model: object) -> dict[str, object]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


class Response(BaseModel):
    """Structured final response."""

    response: str = Field(...)


class Act(BaseModel):
    """Replanner action for legacy generic chains."""

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
                        "title": str(metadata.get("title") or "外部参考资料"),
                        "url": str(metadata.get("source") or "未返回链接"),
                        "summary": str(artifact.get("page_content") or parsed.get("content") or "").replace("\n", " ")[:220],
                    }
                )

    return local_evidence, web_references, tool_names


def _build_generic_template_report(state: PlanExecuteState) -> str:
    task = str(state.get("input", "")).strip() or "AIOps 自定义诊断"
    past_steps = list(state.get("past_steps", []))
    local_evidence, web_references, tool_names = _extract_report_sections(past_steps)

    local_lines = local_evidence[:3] or ["- 当前只拿到了有限的本地 Runbook 参考。"]
    local_block = "\n".join(line if line.startswith("- ") else f"- {line}" for line in local_lines)

    if web_references:
        web_block = "\n".join(
            [
                f"- 资料标题：{item['title']}\n  链接：{item['url']}\n  摘要：{item['summary']}\n  用途：仅作为外部参考，不直接作为本地故障事实证据。"
                for item in web_references
            ]
        )
    else:
        web_block = "- 当前没有补充联网参考资料。"

    evidence_note = "本次结论包含本地 Runbook 参考。" if "retrieve_knowledge" in tool_names else "本次没有命中有效的本地 Runbook。"
    if "web_search" in tool_names:
        evidence_note += " 同时补充了外部公开资料。"

    return dedent(
        f"""
        # AIOps 诊断报告

        ## 诊断对象
        - 任务：{task}
        - 当前结果基于有限的本地知识和执行历史整理，不代表已完成深度现场排查。

        ## 本地知识 / Runbook 证据
        {local_block}

        ## 联网搜索补充资料
        {web_block}

        ## 结论与建议
        - 当前更适合作为受控的参考性结论，而不是现场根因确认结论。
        - 如果后续要进入深度诊断，需要对应 execution_profile 和明确的 required evidence slots。

        ## 风险提示
        - 本次没有执行任何删除、覆盖、pull、prune 或 rm -rf 操作。
        - 涉及镜像、缓存、构建产物或服务重启的动作，都应在人工确认后执行。

        ## 说明
        - {evidence_note}
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
    selected_profile = state.get("selected_profile") or {}
    runtime = get_runtime(selected_profile.get("profile_id") if isinstance(selected_profile, dict) else None)

    if verifier_result and not verifier_result.get("passed", True) and plan_source in LEGACY_GENERIC_PLAN_SOURCES:
        response = state.get("response", "")
        if not response and plan_source == "generic_template_fallback":
            response = _build_generic_template_report(state)
        return {
            "response": response,
            "plan": [],
            "stop_decision": _model_to_dict(
                StopDecision(
                    decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                    reason="Legacy generic diagnosis stops instead of refilling free-text plans.",
                    missing_slots=list(verifier_result.get("missing_evidence", [])),
                )
            ),
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="replanner",
                    status="warning",
                    title="Legacy generic diagnosis finalized with limitations",
                    result_summary="Verifier feedback will not be converted into a new free-text plan",
                )
            ],
        }

    if runtime is not None and plan_source == "investigation_runtime":
        no_progress_rounds = runtime.compute_no_progress_rounds(state)
        state_for_runtime = dict(state)
        state_for_runtime["no_progress_rounds"] = no_progress_rounds
        stop_decision = runtime.decide_stop(state_for_runtime)

        if plan:
            return {
                "no_progress_rounds": no_progress_rounds,
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="replanner",
                        status="success",
                        title=f"{selected_profile.get('profile_id')} investigation continues",
                        result_summary=runtime.summarize_evidence_store(state_for_runtime),
                    )
                ],
            }

        if verifier_result and not verifier_result.get("passed", True):
            next_tasks = runtime.build_follow_up_tasks(state_for_runtime)
            if next_tasks and stop_decision.decision != StopDecisionType.FINALIZE_WITH_LIMITATIONS:
                return {
                    "plan": next_tasks,
                    "response": "",
                    "investigation_round": int(state.get("investigation_round") or 0) + 1,
                    "no_progress_rounds": no_progress_rounds,
                    "trace_events": [
                        create_trace_event(
                            session_id=session_id,
                            node="replanner",
                            status="warning",
                            title=f"{selected_profile.get('profile_id')} requested follow-up evidence",
                            result_summary=" | ".join(task["tool"] for task in next_tasks),
                            metadata={"missing_slots": verifier_result.get("missing_evidence", [])},
                        )
                    ],
                }

            response = state.get("response", "") or runtime.build_report(state_for_runtime)
            if stop_decision.decision == StopDecisionType.CONTINUE:
                stop_decision = StopDecision(
                    decision=StopDecisionType.FINALIZE_WITH_LIMITATIONS,
                    reason="Investigation produced no new follow-up evidence tasks.",
                    missing_slots=list(verifier_result.get("missing_evidence", [])),
                )
            return {
                "response": response,
                "plan": [],
                "no_progress_rounds": no_progress_rounds,
                "stop_decision": _model_to_dict(stop_decision),
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="replanner",
                        status="warning",
                        title=f"{selected_profile.get('profile_id')} finalized with evidence limits",
                        result_summary=stop_decision.reason,
                    )
                ],
            }

        next_tasks = runtime.build_follow_up_tasks(state_for_runtime)
        if next_tasks and stop_decision.decision != StopDecisionType.FINALIZE_WITH_LIMITATIONS:
            return {
                "plan": next_tasks,
                "response": "",
                "investigation_round": int(state.get("investigation_round") or 0) + 1,
                "no_progress_rounds": no_progress_rounds,
                "trace_events": [
                    create_trace_event(
                        session_id=session_id,
                        node="replanner",
                        status="success",
                        title=f"{selected_profile.get('profile_id')} planned follow-up evidence",
                        result_summary=" | ".join(task["tool"] for task in next_tasks),
                    )
                ],
            }

        response = runtime.build_report(state_for_runtime)
        return {
            "response": response,
            "plan": [],
            "no_progress_rounds": no_progress_rounds,
            "stop_decision": _model_to_dict(stop_decision),
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="replanner",
                    status="success",
                    title=f"{selected_profile.get('profile_id')} report drafted",
                    result_summary=runtime.summarize_evidence_store(state_for_runtime),
                )
            ],
        }

    # Legacy compatibility path. Phase 5 can remove this branch after full migration.
    if is_disk_cleanup_request(input_text, matched_skills):
        if verifier_result and not verifier_result.get("passed", True) and plan:
            executed_tools = set(extract_disk_tools_from_steps([step for step, _ in past_steps]))
            requested_tools = [tool for tool in extract_disk_tools_from_steps(plan) if tool]
            if requested_tools and all(tool in executed_tools for tool in requested_tools):
                response = build_disk_cleanup_report(input_text, past_steps)
                response = (
                    f"{response}\n\n## 证据边界说明\n"
                    "- Verifier 判断当前证据仍有缺口，但继续重复 legacy 补查已经不会产生新证据。\n"
                    "- 因此系统在这一轮选择受控收口，而不是继续让旧链路重复增长 Trace。"
                )
                return {
                    "response": response,
                    "plan": [],
                    "trace_events": [
                        create_trace_event(
                            session_id=session_id,
                            node="replanner",
                            status="warning",
                            title="Legacy disk cleanup finalized with evidence limits",
                            result_summary="Repeated follow-up tools produced no new evidence",
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
                        title="Legacy disk cleanup flow continues",
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
                    title="Legacy disk cleanup report drafted",
                    result_summary=response[:280],
                )
            ],
        }

    # Legacy default patrol deep-diagnosis branch. Phase 5 can remove this branch.
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
                            title="Legacy patrol requested more evidence",
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
                        title="Legacy patrol continues",
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
                        title="Legacy patrol requested more evidence",
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
                    title="Legacy patrol report drafted",
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
