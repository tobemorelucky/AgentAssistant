"""Planner node for the governed AIOps workflow."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.disk_cleanup import build_disk_cleanup_plan, is_disk_cleanup_request
from app.agent.aiops.incident_memory import find_similar_incidents
from app.agent.aiops.patrol import (
    build_fallback_tool_plan,
    build_no_alert_report,
    choose_highest_severity_alert,
    required_evidence_summary,
    sanitize_tool_plan_steps,
    summarize_alerts,
    tool_plan_steps_to_dicts,
)
from app.agent.aiops.profile_loader import get_agent_profile_prompt
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.tool_registry import get_aiops_local_tools
from app.agent.aiops.tool_policy import check_tool_policy
from app.agent.aiops.trace import create_trace_event, summarize_result
from app.agent.aiops.utils import format_tools_description, invoke_tool
from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.core.llm_factory import llm_factory
from app.models.aiops import ToolPlanStep
from app.tools import retrieve_knowledge


class GenericPlan(BaseModel):
    """Structured planning output for non-patrol generic tasks."""

    steps: list[str] = Field(default_factory=list)


class StructuredToolPlan(BaseModel):
    """Structured tool plan for controlled default patrol."""

    steps: list[ToolPlanStep] = Field(default_factory=list)


generic_planner_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent(
                """
                You are the AIOps Planner.
                Build a concise 4-7 step execution plan for the user's request.
                The plan should be high level and executable by downstream nodes.

                Available tools:
                {tools_description}

                Context:
                {experience_context}
                """
            ).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)

structured_patrol_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent(
                """
                You are the governed AIOps default patrol planner.

                You MUST return 4-8 structured tool steps.
                Each step must contain:
                - tool
                - args
                - reason
                - evidence_type

                Rules:
                - The target alert is already selected.
                - Prioritize local evidence first: get_active_alerts, query_cpu_metrics, query_memory_metrics,
                  query_process_list, search_log, search_historical_tickets, retrieve_knowledge.
                - Use only available tools.
                - Do not use blocked tools.
                - Prefer read_only and low_risk tools.
                - Do not include dangerous tools in the plan.
                - web_search is optional and can only be used when local runbook retrieval is not enough,
                  the task explicitly asks for public docs/error codes, or external public references are required.
                - web_search can never replace local monitoring, log, or ticket evidence.
                - If you need logs, search_topic_by_service_name should appear before search_log unless topic_id is already known.
                - Cover every required evidence type at least once.
                - Keep args concrete and stable.
                - Do not output prose outside the structured steps.

                Available tools:
                {tools_description}

                Tool policy:
                {tool_policy_summary}

                Agent profile:
                {agent_profile}
                """
            ).strip(),
        ),
        (
            "user",
            dedent(
                """
                User task:
                {task}

                Target alert:
                {target_alert}

                Required evidence:
                {required_evidence}

                Matched skills:
                {matched_skills}

                Similar incidents:
                {similar_incidents}

                Runbook context:
                {runbook_context}
                """
            ).strip(),
        ),
    ]
)

DEFAULT_ALERT_KEYWORDS = (
    "活跃告警",
    "当前系统告警",
    "当前系统是否存在告警",
    "check current alerts",
    "active alerts",
    "current alerts",
)


def _build_generic_fallback_plan(input_text: str, available_tools: list[str]) -> list[str]:
    normalized = (input_text or "").lower()
    steps: list[str] = []
    if "retrieve_knowledge" in available_tools:
        steps.append("调用 retrieve_knowledge 检索相关本地 Runbook、经验文档和处理建议")
    if any(keyword in normalized for keyword in ("docker", "镜像", "image")):
        steps.append("结合本地知识确认镜像名称、标签、仓库来源和冲突触发条件")
        if "web_search" in available_tools:
            steps.append("如本地 Runbook 不足，再调用 web_search 查询 Docker 官方文档或公开排障资料")
        steps.append("整理冲突原因、影响范围和安全处理建议，明确哪些操作需要人工确认")
    else:
        steps.append("基于任务描述整理需要补充的本地证据和排查方向")
        if "web_search" in available_tools:
            steps.append("仅在本地 Runbook 缺失且涉及公开文档时调用 web_search 补充外部参考")
        steps.append("输出证据边界、风险提示和后续建议")
    return steps[:6]


def should_fetch_active_alerts(mode: str, input_text: str) -> bool:
    normalized = (input_text or "").strip().lower()
    if mode == "default":
        return True
    return any(keyword.lower() in normalized for keyword in DEFAULT_ALERT_KEYWORDS)


def _skill_context(skills: list[dict[str, Any]]) -> str:
    if not skills:
        return "No matched skills."
    return "\n\n".join(skill.get("summary", "") or skill.get("description", "") or skill.get("name", "") for skill in skills)


def _incident_context(similar_incidents: list[dict[str, Any]]) -> str:
    if not similar_incidents:
        return "No similar incident cases."
    return "\n\n".join(
        f"- Task: {incident.get('user_task', '')}\n"
        f"  Root cause: {incident.get('root_cause', '')}\n"
        f"  Tools: {', '.join(incident.get('tools_used', []) or [])}"
        for incident in similar_incidents
    )


def _tool_policy_summary(tool_names: list[str]) -> str:
    lines = []
    for tool_name in sorted(set(tool_names)):
        decision = check_tool_policy(tool_name)
        lines.append(f"- {tool_name}: {decision['level']} ({decision['decision']})")
    return "\n".join(lines)


async def planner(state: PlanExecuteState) -> dict[str, Any]:
    """LangGraph node: create a diagnosis plan."""
    logger.info("=== Planner ===")
    input_text = state.get("input", "")
    mode = state.get("mode", "default")
    session_id = state.get("session_id", "default")
    matched_skills = state.get("matched_skills", [])

    local_tools = get_aiops_local_tools()
    mcp_client = await get_mcp_client_with_retry()
    mcp_tools = await mcp_client.get_tools()
    all_tools = local_tools + mcp_tools
    tool_map = {tool.name if hasattr(tool, "name") else str(tool): tool for tool in all_tools}
    tool_names = list(tool_map.keys())

    similar_incidents = find_similar_incidents(input_text, limit=3)
    trace_events: list[dict[str, Any]] = []
    active_alerts = list(state.get("active_alerts", []) or [])
    target_alert = state.get("target_alert")

    if is_disk_cleanup_request(input_text, matched_skills):
        plan_steps = build_disk_cleanup_plan()
        trace_events.append(
            create_trace_event(
                session_id=session_id,
                node="planner",
                status="success",
                title="Disk cleanup plan generated",
                result_summary=" | ".join(plan_steps[:3]),
                metadata={"matched_skills": [skill.get("name") for skill in matched_skills]},
            )
        )
        return {
            "plan": plan_steps,
            "similar_incidents": similar_incidents,
            "trace_events": trace_events,
        }

    if should_fetch_active_alerts(mode, input_text):
        active_alert_tool = tool_map.get("get_active_alerts") or tool_map.get("list_active_alerts")
        if active_alert_tool is not None:
            alert_result = await invoke_tool(active_alert_tool, {"include_resolved": False})
            active_alerts = list(alert_result.get("active_alerts") or alert_result.get("alerts") or [])
            target_alert = choose_highest_severity_alert(active_alerts)
            trace_events.append(
                create_trace_event(
                    session_id=session_id,
                    node="tool_call",
                    status="success",
                    title="Fetched active alerts for default patrol",
                    tool_name=getattr(active_alert_tool, "name", "get_active_alerts"),
                    tool_args={"include_resolved": False},
                    result_summary=summarize_result(alert_result),
                    metadata={
                        "active_alert_count": len(active_alerts),
                        "target_alert": target_alert or {},
                        "source": "planner",
                    },
                )
            )

            if not active_alerts:
                report = build_no_alert_report()
                trace_events.append(
                    create_trace_event(
                        session_id=session_id,
                        node="planner",
                        status="success",
                        title="No active alerts found",
                        result_summary="Patrol completed with no active alerts",
                    )
                )
                return {
                    "active_alerts": [],
                    "target_alert": None,
                    "response": report,
                    "trace_events": trace_events,
                }

            if target_alert:
                blocked_tools = {
                    name for name in tool_names if check_tool_policy(name).get("decision") == "reject"
                }
                available_tools = set(tool_names)
                structured_steps: list[ToolPlanStep] = []
                runbook_context = ""

                try:
                    runbook_context = await retrieve_knowledge.ainvoke(
                        {"query": f"{target_alert.get('service_name', '')} {target_alert.get('alert_name', '')} runbook"}
                    )
                except Exception as exc:  # pragma: no cover - best effort
                    logger.warning(f"retrieve_knowledge failed during patrol planning: {exc}")

                if config.get_llm_api_key():
                    try:
                        llm = llm_factory.create_qwen_chat_model(
                            preferred_model=config.rag_model,
                            temperature=0,
                            streaming=True,
                        )
                        chain = structured_patrol_prompt | llm.with_structured_output(StructuredToolPlan)
                        llm_plan = await chain.ainvoke(
                            {
                                "task": input_text,
                                "target_alert": summarize_result(target_alert),
                                "required_evidence": summarize_result(
                                    required_evidence_summary(target_alert, matched_skills)
                                ),
                                "matched_skills": _skill_context(matched_skills),
                                "similar_incidents": _incident_context(similar_incidents),
                                "runbook_context": runbook_context or "No runbook context.",
                                "tools_description": format_tools_description(all_tools),
                                "tool_policy_summary": _tool_policy_summary(tool_names),
                                "agent_profile": get_agent_profile_prompt(),
                            }
                        )
                        structured_steps = list(llm_plan.steps or [])
                    except Exception as exc:
                        logger.warning(f"Structured patrol planning failed, fallback to template: {exc}")

                sanitized_steps = sanitize_tool_plan_steps(
                    structured_steps,
                    target_alert=target_alert,
                    matched_skills=matched_skills,
                    available_tools=available_tools,
                    blocked_tools=blocked_tools,
                )
                if not sanitized_steps:
                    sanitized_steps = build_fallback_tool_plan(target_alert, matched_skills)

                trace_events.append(
                    create_trace_event(
                        session_id=session_id,
                        node="planner",
                        status="success",
                        title=f"Controlled patrol plan for {target_alert.get('service_name', 'unknown-service')}",
                        result_summary=" | ".join(step.tool for step in sanitized_steps[:4]),
                        metadata={
                            "active_alerts": active_alerts,
                            "target_alert": target_alert,
                            "required_evidence": required_evidence_summary(target_alert, matched_skills),
                        },
                    )
                )
                return {
                    "plan": tool_plan_steps_to_dicts(sanitized_steps),
                    "active_alerts": active_alerts,
                    "target_alert": target_alert,
                    "similar_incidents": similar_incidents,
                    "trace_events": trace_events,
                }

    agent_profile = get_agent_profile_prompt()
    skill_context = _skill_context(matched_skills)
    incident_context = _incident_context(similar_incidents)

    experience_docs = ""
    try:
        context_str = await retrieve_knowledge.ainvoke({"query": input_text})
        if context_str and str(context_str).strip():
            experience_docs = str(context_str)
    except Exception as exc:  # pragma: no cover - best effort enrichment
        logger.warning(f"retrieve_knowledge failed in planner context: {exc}")

    tools_description = format_tools_description(all_tools)
    plan_source = "generic_llm"
    try:
        llm = llm_factory.create_qwen_chat_model(
            preferred_model=config.rag_model,
            temperature=0,
            streaming=True,
        )
        planner_chain = generic_planner_prompt | llm.with_structured_output(GenericPlan)

        plan_result = await planner_chain.ainvoke(
            {
                "messages": [("user", input_text)],
                "tools_description": tools_description,
                "experience_context": dedent(
                    f"""
                    ## AGENT Profile
                    {agent_profile}

                    ## Matched Skills
                    {skill_context}

                    ## Similar Incident Cases
                    {incident_context}

                    ## Active Alerts
                    {summarize_alerts(active_alerts)}

                    ## Selected Target Alert
                    {summarize_result(target_alert)}

                    ## Knowledge Context
                    {experience_docs or "No additional runbook context."}
                    """
                ).strip(),
            }
        )
        plan_steps = plan_result.steps if isinstance(plan_result, GenericPlan) else plan_result.get("steps", [])
        if not isinstance(plan_steps, list) or not plan_steps:
            raise ValueError("generic planner returned empty steps")
        trace_events.append(
            create_trace_event(
                session_id=session_id,
                node="planner",
                status="success",
                title=f"Planner generated {len(plan_steps)} generic steps",
                result_summary=" | ".join(str(step) for step in plan_steps[:3]),
                metadata={
                    "matched_skills": [skill.get("name") for skill in matched_skills],
                    "similar_incidents": similar_incidents,
                },
            )
        )
    except Exception as exc:
        logger.warning(f"Generic planner failed, fallback to template plan: {exc}")
        plan_steps = _build_generic_fallback_plan(input_text, tool_names)
        plan_source = "generic_template_fallback"
        trace_events.append(
            create_trace_event(
                session_id=session_id,
                node="planner",
                status="warning",
                title="Generic planner fallback plan generated",
                result_summary=" | ".join(plan_steps[:3]),
                metadata={"reason": str(exc)},
            )
        )
    return {
        "plan": plan_steps,
        "plan_source": plan_source,
        "similar_incidents": similar_incidents,
        "active_alerts": active_alerts,
        "target_alert": target_alert,
        "trace_events": trace_events,
    }
