"""Planner node for the governed AIOps workflow."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.disk_cleanup import build_disk_cleanup_plan, is_disk_cleanup_request
from app.agent.aiops.incident_memory import find_similar_incidents
from app.agent.aiops.investigation import (
    build_evidence_store,
    build_no_alert_patrol_report,
    build_unconfigured_alert_source_report,
    build_unsupported_profile_report,
    decide_stop_action,
    get_profile,
    get_runtime,
    resolve_alert_profile_id,
    select_target_alert,
    supports_profile_execution,
)
from app.agent.aiops.patrol import build_fallback_tool_plan, format_tool_plan_step, summarize_alerts
from app.agent.aiops.profile_loader import get_agent_profile_prompt
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.tool_registry import get_aiops_local_tools
from app.agent.aiops.trace import create_trace_event, summarize_result
from app.agent.aiops.utils import format_tools_description, invoke_tool
from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.core.llm_factory import llm_factory
from app.tools import retrieve_knowledge


def _model_to_dict(model: Any) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


class GenericPlan(BaseModel):
    """Structured planning output for legacy generic tasks."""

    steps: list[str] = Field(default_factory=list)


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
        steps.append("基于当前已收集资料整理排查关注点，先确认症状范围、资源对象和触发条件")
        if "web_search" in available_tools:
            steps.append("如本地知识仍不足，再调用 web_search 补充公开官方文档或错误说明")
        steps.append("整理当前证据、证据缺口、风险提示和后续建议")
    return steps[:6]


def build_default_alert_plan(target_alert: dict[str, Any]) -> list[str]:
    """Compatibility helper used by tests and docs."""
    return [format_tool_plan_step(step) for step in build_fallback_tool_plan(target_alert)]


def _skill_context(skills: list[dict[str, Any]]) -> str:
    if not skills:
        return "No matched skills."
    return "\n\n".join(
        skill.get("summary", "") or skill.get("description", "") or skill.get("name", "")
        for skill in skills
    )


def _incident_context(similar_incidents: list[dict[str, Any]]) -> str:
    if not similar_incidents:
        return "No similar incident cases."
    return "\n\n".join(
        f"- Task: {incident.get('user_task', '')}\n"
        f"  Root cause: {incident.get('root_cause', '')}\n"
        f"  Tools: {', '.join(incident.get('tools_used', []) or [])}"
        for incident in similar_incidents
    )


def _build_controlled_profile_gap_report(
    input_text: str,
    *,
    diagnosis_intent: str,
    matched_skills: list[dict[str, Any]],
    selected_profile: dict[str, Any] | None,
) -> str:
    reference_skills = [skill["name"] for skill in matched_skills if skill.get("skill_mode") == "reference_playbook"]
    execution_skills = [skill["name"] for skill in matched_skills if skill.get("skill_mode") == "execution_profile"]
    skill_lines = reference_skills or execution_skills or ["未命中 execution_profile，仅命中参考型 Playbook"]
    skill_block = "\n".join(f"- {line}" for line in skill_lines)

    profile_label = selected_profile.get("profile_id") if isinstance(selected_profile, dict) else None
    if profile_label and not supports_profile_execution(profile_label):
        gap_reason = f"已命中 Profile `{profile_label}`，但当前还没有接入可执行的 Investigation Runtime。"
    else:
        gap_reason = "当前没有匹配到可执行的结构化诊断 Profile。"

    return dedent(
        f"""
        # AIOps 受控诊断结果

        ## 当前请求
        - 任务：{input_text}
        - Diagnosis intent：{diagnosis_intent}
        - {gap_reason}

        ## 已命中的技能 / Playbook
        {skill_block}

        ## 当前处理结果
        - 为避免再次进入不可靠的 legacy generic 长链，本次没有继续执行深度自主排查。
        - 参考型 Playbook 只会作为知识参考，不会直接驱动工具执行计划。

        ## 后续建议
        - 需要为该类问题补充 execution_profile，并定义 required_evidence_slots、stop_rules 和 verifier 规则。
        - 在对应 Profile 接入新的 Investigation Runtime 之前，系统会优先选择受控结束，而不是继续做无证据长链推断。
        """
    ).strip()


def _build_controlled_profile_gap_result(
    *,
    session_id: str,
    input_text: str,
    diagnosis_intent: str,
    matched_skills: list[dict[str, Any]],
    selected_profile: dict[str, Any] | None,
    similar_incidents: list[dict[str, Any]],
) -> dict[str, Any]:
    report = _build_controlled_profile_gap_report(
        input_text,
        diagnosis_intent=diagnosis_intent,
        matched_skills=matched_skills,
        selected_profile=selected_profile,
    )
    evidence_store = build_evidence_store(None)
    stop_decision = decide_stop_action(
        profile=None,
        no_progress_rounds=0,
        hard_limit_reached=True,
        reason="No execution_profile matched for controlled custom diagnosis.",
    )
    trace_event = create_trace_event(
        session_id=session_id,
        node="planner",
        status="warning",
        title="Controlled custom diagnosis stopped before legacy generic chain",
        result_summary="No execution_profile matched",
        metadata={
            "diagnosis_intent": diagnosis_intent,
            "matched_skills": [skill.get("name") for skill in matched_skills],
            "selected_profile": selected_profile or {},
        },
    )
    return {
        "response": report,
        "plan_source": "controlled_no_profile",
        "similar_incidents": similar_incidents,
        "selected_profile": selected_profile,
        "evidence_store": evidence_store,
        "stop_decision": _model_to_dict(stop_decision),
        "trace_events": [trace_event],
    }


async def _dispatch_default_patrol(
    *,
    state: PlanExecuteState,
    session_id: str,
    selected_profile: dict[str, Any] | None,
    similar_incidents: list[dict[str, Any]],
    tool_map: dict[str, Any],
) -> dict[str, Any]:
    trace_events: list[dict[str, Any]] = []
    active_alert_tool = (
        tool_map.get("get_patrol_alerts")
        or tool_map.get("get_active_alerts")
        or tool_map.get("list_active_alerts")
    )
    if active_alert_tool is None:
        report = dedent(
            """
            # AIOps 巡检报告

            ## 巡检结果
            - 未能执行活跃告警发现，因为当前没有可用的 `get_active_alerts / list_active_alerts` 工具。
            """
        ).strip()
        trace_events.append(
            create_trace_event(
                session_id=session_id,
                node="planner",
                status="error",
                title="Patrol dispatch failed to find alert discovery tool",
                result_summary="No active-alert tool available",
            )
        )
        return {
            "response": report,
            "plan_source": "patrol_dispatch_no_tool",
            "trace_events": trace_events,
        }

    alert_result = await invoke_tool(active_alert_tool, {"include_resolved": False})
    if str(alert_result.get("provider") or "").lower() == "disabled":
        report = build_unconfigured_alert_source_report()
        trace_events.append(
            create_trace_event(
                session_id=session_id,
                node="planner",
                status="success",
                title="Patrol completed without configured alert provider",
                result_summary="Alert provider disabled",
            )
        )
        return {
            "active_alerts": [],
            "target_alert": None,
            "response": report,
            "plan_source": "patrol_dispatch_disabled",
            "selected_profile": selected_profile,
            "trace_events": trace_events,
        }

    active_alerts = list(alert_result.get("active_alerts") or alert_result.get("alerts") or [])
    target_alert = select_target_alert(active_alerts)
    trace_events.append(
        create_trace_event(
            session_id=session_id,
            node="tool_call",
            status="success",
            title="Fetched active alerts for patrol dispatch",
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
        report = build_no_alert_patrol_report()
        trace_events.append(
            create_trace_event(
                session_id=session_id,
                node="planner",
                status="success",
                title="Patrol completed with no active alerts",
                result_summary="No active alerts detected",
            )
        )
        return {
            "active_alerts": [],
            "target_alert": None,
            "response": report,
            "plan_source": "patrol_dispatch_no_alert",
            "selected_profile": selected_profile,
            "trace_events": trace_events,
        }

    dispatched_profile_id = resolve_alert_profile_id(target_alert)
    dispatched_profile = get_profile(dispatched_profile_id) if dispatched_profile_id else None
    dispatched_runtime = get_runtime(dispatched_profile_id) if dispatched_profile_id else None
    if dispatched_profile and dispatched_runtime:
        dispatched_profile_dict = _model_to_dict(dispatched_profile)
        dispatch_state = dict(state)
        dispatch_state.update(
            {
                "selected_profile": dispatched_profile_dict,
                "active_alerts": active_alerts,
                "target_alert": target_alert,
                "evidence_store": build_evidence_store(dispatched_profile),
                "investigation_round": 0,
                "no_progress_rounds": 0,
                "last_investigation_slot": None,
                "stop_decision": None,
            }
        )
        plan_steps = dispatched_runtime.build_initial_tasks(dispatch_state)
        trace_events.append(
            create_trace_event(
                session_id=session_id,
                node="planner",
                status="success",
                title=f"Patrol dispatched to {dispatched_profile_id}",
                result_summary=" | ".join(str(step.get("tool")) for step in plan_steps[:4]),
                metadata={
                    "active_alerts": active_alerts,
                    "target_alert": target_alert,
                    "selected_profile": dispatched_profile_dict,
                },
            )
        )
        return {
            "plan": plan_steps,
            "plan_source": "investigation_runtime",
            "selected_profile": dispatched_profile_dict,
            "evidence_store": build_evidence_store(dispatched_profile),
            "investigation_round": 0,
            "no_progress_rounds": 0,
            "last_investigation_slot": None,
            "active_alerts": active_alerts,
            "target_alert": target_alert,
            "similar_incidents": similar_incidents,
            "trace_events": trace_events,
        }

    report = build_unsupported_profile_report(target_alert)
    trace_events.append(
        create_trace_event(
            session_id=session_id,
            node="planner",
            status="warning",
            title="Patrol found unsupported alert profile",
            result_summary=summarize_result(target_alert),
        )
    )
    return {
        "response": report,
        "plan_source": "patrol_dispatch_unsupported_profile",
        "selected_profile": selected_profile,
        "active_alerts": active_alerts,
        "target_alert": target_alert,
        "trace_events": trace_events,
    }


async def planner(state: PlanExecuteState) -> dict[str, Any]:
    """LangGraph node: create a diagnosis plan."""
    logger.info("=== Planner ===")
    input_text = state.get("input", "")
    mode = state.get("mode", "default")
    session_id = state.get("session_id", "default")
    matched_skills = state.get("matched_skills", [])
    diagnosis_intent = state.get("diagnosis_intent", "")
    selected_profile = state.get("selected_profile")
    selected_profile_id = selected_profile.get("profile_id") if isinstance(selected_profile, dict) else None

    local_tools = get_aiops_local_tools()
    mcp_client = await get_mcp_client_with_retry()
    mcp_tools = await mcp_client.get_tools()
    all_tools = local_tools + mcp_tools
    tool_map = {tool.name if hasattr(tool, "name") else str(tool): tool for tool in all_tools}
    tool_names = list(tool_map.keys())

    similar_incidents = find_similar_incidents(input_text, limit=3)
    active_alerts = list(state.get("active_alerts", []) or [])
    target_alert = state.get("target_alert")
    trace_events: list[dict[str, Any]] = []

    if mode == "default" and selected_profile_id == "patrol_dispatch_profile":
        return await _dispatch_default_patrol(
            state=state,
            session_id=session_id,
            selected_profile=selected_profile,
            similar_incidents=similar_incidents,
            tool_map=tool_map,
        )

    runtime = get_runtime(selected_profile_id)
    if runtime is not None:
        profile = get_profile(selected_profile_id)
        plan_steps = runtime.build_initial_tasks(dict(state))
        trace_events.append(
            create_trace_event(
                session_id=session_id,
                node="planner",
                status="success",
                title=f"Investigation runtime plan generated for {selected_profile_id}",
                result_summary=" | ".join(str(step.get("tool")) for step in plan_steps[:4]),
                metadata={
                    "profile_id": selected_profile_id,
                    "plan_source": "investigation_runtime",
                    "diagnosis_intent": diagnosis_intent,
                },
            )
        )
        return {
            "plan": plan_steps,
            "plan_source": "investigation_runtime",
            "selected_profile": selected_profile,
            "evidence_store": state.get("evidence_store") or build_evidence_store(profile),
            "similar_incidents": similar_incidents,
            "trace_events": trace_events,
        }

    # Legacy compatibility path. Phase 5 can remove this branch after full migration.
    if is_disk_cleanup_request(input_text, matched_skills):
        plan_steps = build_disk_cleanup_plan()
        trace_events.append(
            create_trace_event(
                session_id=session_id,
                node="planner",
                status="success",
                title="Legacy disk cleanup plan generated",
                result_summary=" | ".join(plan_steps[:3]),
                metadata={"matched_skills": [skill.get("name") for skill in matched_skills], "legacy": True},
            )
        )
        return {
            "plan": plan_steps,
            "similar_incidents": similar_incidents,
            "selected_profile": selected_profile,
            "trace_events": trace_events,
        }

    if (
        mode == "custom"
        and not config.aiops_allow_legacy_generic_diagnosis
        and not (selected_profile and supports_profile_execution(selected_profile.get("profile_id")))
    ):
        return _build_controlled_profile_gap_result(
            session_id=session_id,
            input_text=input_text,
            diagnosis_intent=diagnosis_intent or "knowledge_only",
            matched_skills=matched_skills,
            selected_profile=selected_profile,
            similar_incidents=similar_incidents,
        )

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
        "selected_profile": selected_profile,
        "similar_incidents": similar_incidents,
        "active_alerts": active_alerts,
        "target_alert": target_alert,
        "trace_events": trace_events,
    }
