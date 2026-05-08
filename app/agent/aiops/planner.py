"""Planner node for the governed AIOps workflow."""

from __future__ import annotations

from textwrap import dedent
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_qwq import ChatQwen
from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.disk_cleanup import (
    build_disk_cleanup_plan,
    is_disk_cleanup_request,
)
from app.agent.aiops.incident_memory import find_similar_incidents
from app.agent.aiops.profile_loader import get_agent_profile_prompt
from app.agent.aiops.state import PlanExecuteState
from app.agent.aiops.trace import create_trace_event, summarize_result
from app.agent.aiops.utils import format_tools_description, invoke_tool
from app.agent.mcp_client import get_mcp_client_with_retry
from app.config import config
from app.tools import get_current_time, retrieve_knowledge


class Plan(BaseModel):
    """Structured planning output."""

    steps: list[str] = Field(default_factory=list)


planner_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent(
                """
                你是一个可治理的 AIOps Planner。请基于用户任务、Agent Profile、已命中的 Skill、可用工具、
                相似 Incident Case 和已知告警信息，生成一个精炼、可执行的诊断计划。

                规则：
                - 计划步骤必须能被 Executor 执行，每一步都尽量明确提到要调用的工具。
                - 优先采集证据，再做判断，不要先下结论。
                - 计划步骤数量控制在 4 到 7 步。
                - 如果已有 active alerts 或 target alert，计划必须围绕具体 service_name / alert_name 展开。

                可用工具：
                {tools_description}

                额外上下文：
                {experience_context}
                """
            ).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)

DEFAULT_ALERT_KEYWORDS = (
    "检查当前系统告警",
    "当前系统是否存在告警",
    "活跃告警",
    "当前系统告警",
)
SEVERITY_ORDER = {"critical": 4, "high": 3, "warning": 2, "info": 1, "low": 1}


def should_fetch_active_alerts(mode: str, input_text: str) -> bool:
    """Whether this request should start from active alert discovery."""
    normalized = (input_text or "").strip().lower()
    if mode == "default":
        return True
    return any(keyword.lower() in normalized for keyword in DEFAULT_ALERT_KEYWORDS)


def choose_highest_severity_alert(alerts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the highest severity alert."""
    if not alerts:
        return None
    return sorted(
        alerts,
        key=lambda alert: (
            -SEVERITY_ORDER.get(str(alert.get("severity", "")).lower(), 0),
            str(alert.get("service_name", "")),
            str(alert.get("alert_name", "")),
        ),
    )[0]


def build_no_alert_report() -> str:
    """Return a structured patrol report when no active alerts are present."""
    return dedent(
        """
        # 当前未检测到活跃告警

        ## 巡检结论
        - 当前 mock 监控源中未发现处于 firing 状态的活跃告警。

        ## 已执行检查
        - 查询当前活跃告警列表
        - 校验 active alerts 返回结果

        ## 影响范围
        - 当前没有发现需要进入服务级根因分析的告警对象。

        ## 风险提示
        - 本次结论只基于当前可访问的 mock 告警源。
        - 如果后续接入真实监控平台，建议继续保留 active alerts 作为默认巡检第一步。

        ## 后续建议
        - 保持当前巡检频率
        - 持续完善告警与 runbook 的映射关系
        """
    ).strip()


def build_default_alert_plan(alert: dict[str, Any]) -> list[str]:
    """Build a deterministic patrol plan around the selected alert."""
    service_name = alert.get("service_name", "unknown-service")
    alert_name = alert.get("alert_name", "unknown-alert")
    instance = alert.get("instance", "unknown-instance")
    duration = alert.get("duration", "unknown")
    return [
        f"调用 get_service_info 获取 {service_name} 的服务拓扑、实例和依赖信息，确认告警实例 {instance} 的上下文。",
        f"调用 query_cpu_metrics 查询 {service_name} 最近 {duration} 的 CPU 指标，核对 {alert_name} 是否与 CPU 上升一致。",
        f"调用 query_memory_metrics 查询 {service_name} 的内存趋势，排除资源争用或内存压力共同触发的情况。",
        f"调用 query_process_list 查看 {service_name} 各实例进程占用，定位是否为单实例热点或异常进程。",
        f"调用 search_historical_tickets 检索 {service_name} 与 {alert_name} 相关历史工单，参考已有根因与处置经验。",
        f"调用 retrieve_knowledge 检索 {service_name} / {alert_name} 对应 runbook，补充修复建议与风险提示。",
    ]


def summarize_alerts(alerts: list[dict[str, Any]]) -> str:
    """Create compact alert summaries for prompts and traces."""
    if not alerts:
        return "No active alerts."
    return "; ".join(
        (
            f"{alert.get('service_name', 'unknown')}/"
            f"{alert.get('alert_name', 'unknown')} "
            f"[{alert.get('severity', 'unknown')}]"
        )
        for alert in alerts[:5]
    )


async def planner(state: PlanExecuteState) -> dict[str, Any]:
    """LangGraph node: create a diagnosis plan."""
    logger.info("=== Planner ===")
    input_text = state.get("input", "")
    mode = state.get("mode", "default")
    session_id = state.get("session_id", "default")
    matched_skills = state.get("matched_skills", [])

    local_tools = [get_current_time, retrieve_knowledge]
    mcp_client = await get_mcp_client_with_retry()
    mcp_tools = await mcp_client.get_tools()
    all_tools = local_tools + mcp_tools
    tool_map = {tool.name if hasattr(tool, "name") else str(tool): tool for tool in all_tools}

    similar_incidents = find_similar_incidents(input_text, limit=3)
    alerts_trace_events: list[dict[str, Any]] = []
    active_alerts = list(state.get("active_alerts", []) or [])
    target_alert = state.get("target_alert")

    if is_disk_cleanup_request(input_text, matched_skills):
        plan_steps = build_disk_cleanup_plan()
        trace_event = create_trace_event(
            session_id=session_id,
            node="planner",
            status="success",
            title="Disk cleanup plan generated",
            result_summary=" | ".join(plan_steps[:3]),
            metadata={"matched_skills": [skill.get("name") for skill in matched_skills]},
        )
        return {
            "plan": plan_steps,
            "similar_incidents": similar_incidents,
            "trace_events": [trace_event],
        }

    if should_fetch_active_alerts(mode, input_text):
        active_alert_tool = tool_map.get("get_active_alerts") or tool_map.get("list_active_alerts")
        if active_alert_tool is not None:
            alert_result = await invoke_tool(active_alert_tool, {"include_resolved": False})
            active_alerts = list(alert_result.get("active_alerts") or alert_result.get("alerts") or [])
            target_alert = choose_highest_severity_alert(active_alerts)
            alerts_trace_events.append(
                create_trace_event(
                    session_id=session_id,
                    node="tool_call",
                    status="success",
                    title="Fetched active alerts for patrol",
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
                alerts_trace_events.append(
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
                    "trace_events": alerts_trace_events,
                }

            if target_alert:
                plan_steps = build_default_alert_plan(target_alert)
                alerts_trace_events.append(
                    create_trace_event(
                        session_id=session_id,
                        node="planner",
                        status="success",
                        title=f"Default patrol plan for {target_alert.get('service_name', 'unknown-service')}",
                        result_summary=" | ".join(plan_steps[:3]),
                        metadata={"active_alerts": active_alerts, "target_alert": target_alert},
                    )
                )
                return {
                    "plan": plan_steps,
                    "active_alerts": active_alerts,
                    "target_alert": target_alert,
                    "similar_incidents": similar_incidents,
                    "trace_events": alerts_trace_events,
                }

    agent_profile = get_agent_profile_prompt()
    skill_context = (
        "\n\n".join(skill.get("summary", "") for skill in matched_skills) if matched_skills else "No matched skills."
    )
    incident_context = (
        "\n\n".join(
            f"- 用户任务: {incident['user_task']}\n"
            f"  Root cause summary: {incident['root_cause']}\n"
            f"  Tools: {', '.join(incident['tools_used'])}"
            for incident in similar_incidents
        )
        if similar_incidents
        else "No similar incident cases."
    )

    experience_docs = ""
    try:
        context_str = await retrieve_knowledge.ainvoke({"query": input_text})
        if context_str and context_str.strip():
            experience_docs = context_str
    except Exception as exc:  # pragma: no cover - best effort enrichment
        logger.warning(f"retrieve_knowledge failed in planner context: {exc}")

    tools_description = format_tools_description(all_tools)
    llm = ChatQwen(model=config.rag_model, api_key=config.dashscope_api_key, temperature=0)
    planner_chain = planner_prompt | llm.with_structured_output(Plan)

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

    plan_steps = plan_result.steps if isinstance(plan_result, Plan) else plan_result.get("steps", [])
    trace_event = create_trace_event(
        session_id=session_id,
        node="planner",
        status="success",
        title=f"Planner generated {len(plan_steps)} steps",
        result_summary=" | ".join(plan_steps[:3]),
        metadata={
            "matched_skills": [skill.get("name") for skill in matched_skills],
            "similar_incidents": similar_incidents,
        },
    )
    return {
        "plan": plan_steps,
        "similar_incidents": similar_incidents,
        "active_alerts": active_alerts,
        "target_alert": target_alert,
        "trace_events": alerts_trace_events + [trace_event],
    }
