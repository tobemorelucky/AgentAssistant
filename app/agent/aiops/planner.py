"""Planner 节点：制定执行计划。"""

from __future__ import annotations

from textwrap import dedent
from typing import Any, Dict, List

from langchain_core.prompts import ChatPromptTemplate
from langchain_qwq import ChatQwen
from loguru import logger
from pydantic import BaseModel, Field

from app.agent.aiops.incident_memory import find_similar_incidents
from app.agent.aiops.profile_loader import get_agent_profile_prompt
from app.agent.aiops.trace import create_trace_event, summarize_result
from app.config import config
from app.tools import get_current_time, retrieve_knowledge
from app.agent.mcp_client import get_mcp_client_with_retry
from .state import PlanExecuteState
from .utils import format_tools_description, invoke_tool


class Plan(BaseModel):
    """计划的输出格式"""
    steps: List[str] = Field(
        description="完成任务所需的不同步骤。这些步骤应该按顺序执行，每一步都建立在前一步的基础上。"
    )


# Planner 提示词
planner_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            dedent("""
                作为一个专家级别的规划者，你需要将复杂的任务分解为可执行的步骤。

                可用工具列表（用于制定计划时参考）：

                {tools_description}

                注意：你的职责是制定计划，实际的工具调用由 Executor 负责执行。

                {experience_context}

                对于给定的任务，请创建一个简单的、逐步的计划来完成它。计划应该：
                - 将任务分解为逻辑上独立的步骤
                - 每个步骤应该明确使用哪些工具(如果需要工具的话)来获取信息, 最好能同时提供工具执行所需要的参数
                - 步骤之间应该有清晰的依赖关系
                - 步骤描述要具体、可操作
                - **如果有相关经验文档，请参考其中的方法和步骤制定计划**

                示例输入："分析当前系统的性能问题"
                示例输出（假设有对应工具）：
                步骤1: 使用 get_metrics 工具收集系统的 CPU 和内存使用情况
                步骤2: 使用 query_logs 工具检查最近的错误日志
                步骤3: 使用 query_database 工具分析慢查询日志
                步骤4: 综合以上信息生成性能分析报告
            """).strip(),
        ),
        ("placeholder", "{messages}"),
    ]
)

DEFAULT_ALERT_KEYWORDS = (
    "当前系统",
    "活跃告警",
    "当前未检测到活跃告警",
    "检查当前系统是否存在活跃告警",
)
SEVERITY_ORDER = {"critical": 4, "high": 3, "warning": 2, "info": 1, "low": 1}


def should_fetch_active_alerts(mode: str, input_text: str) -> bool:
    """Whether this request should start from active alert discovery."""
    normalized = (input_text or "").strip().lower()
    if mode == "default":
        return True
    return any(keyword.lower() in normalized for keyword in DEFAULT_ALERT_KEYWORDS)


def choose_highest_severity_alert(alerts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Select the highest severity alert, preserving stable ordering for ties."""
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
        # 告警分析报告

        ## 活跃告警清单
        - 当前未检测到活跃告警

        ## 执行轨迹摘要
        - 已执行活跃告警巡检
        - 已核对监控系统返回的 active alerts 数据
        - 因未发现活跃告警，未继续进入服务级深度诊断

        ## 根因分析
        - 当前无可归因的活跃告警
        - 未发现需要进一步分析的异常服务

        ## 关键证据
        - 监控系统 active alerts 查询结果为空

        ## 影响范围
        - 当前未发现受活跃告警影响的服务
        - 时间范围：本次巡检执行时刻
        - 风险范围：无明显即时风险

        ## 风险提示
        - 本次巡检结论仅基于当前 mock 监控与知识库数据
        - 若后续出现瞬时告警或外部系统异常，请重新发起诊断

        ## 处理建议
        - 继续保持常规巡检
        - 如需针对特定服务做预防性排查，可使用自定义诊断模式
        """
    ).strip()


def build_default_alert_plan(alert: dict[str, Any]) -> list[str]:
    """Build a deterministic patrol plan around the selected alert."""
    service_name = alert.get("service_name", "unknown-service")
    alert_name = alert.get("alert_name", "unknown-alert")
    instance = alert.get("instance", "unknown-instance")
    duration = alert.get("duration", "unknown")
    return [
        (
            f"使用 get_service_info 查询 {service_name} 的服务拓扑和实例详情，"
            f"确认告警实例 {instance} 与运行状态。"
        ),
        (
            f"使用 query_cpu_metrics 分析 {service_name} 最近 30 分钟 CPU 指标，"
            f"验证告警 {alert_name} 在持续 {duration} 内的触发趋势。"
        ),
        f"使用 query_memory_metrics 检查 {service_name} 同时间窗的内存和资源压力，排除伴生资源瓶颈。",
        (
            f"先使用 search_topic_by_service_name 查找 {service_name} 的日志主题，"
            "再结合 get_current_timestamp 和 search_log 查询最近 30 分钟 ERROR/WARN 日志。"
        ),
        f"使用 search_historical_tickets 查询 {service_name} 的历史工单，核对是否存在相似 {alert_name} 事件。",
        f"使用 retrieve_knowledge 检索 {service_name} / {alert_name} 对应的 runbook，形成修复建议和风险提示。",
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


async def planner(state: PlanExecuteState) -> Dict[str, Any]:
    """
    规划节点：根据用户输入生成执行计划

    流程：
    1. 先查询内部文档，获取相关经验和最佳实践
    2. 基于经验文档和可用工具制定执行计划
    """
    logger.info("=== Planner：制定执行计划 ===")

    input_text = state.get("input", "")
    mode = state.get("mode", "default")
    session_id = state.get("session_id", "default")
    logger.info(f"用户输入: {input_text}")

    try:
        # 获取本地工具
        local_tools = [get_current_time, retrieve_knowledge]

        # 获取 MCP 工具
        mcp_client = await get_mcp_client_with_retry()
        mcp_tools = await mcp_client.get_tools()
        all_tools = local_tools + mcp_tools
        tool_map = {
            tool.name if hasattr(tool, "name") else str(tool): tool
            for tool in all_tools
        }

        agent_profile = get_agent_profile_prompt()
        matched_skills = state.get("matched_skills", [])
        skill_context = (
            "\n\n".join(skill.get("summary", "") for skill in matched_skills)
            if matched_skills
            else "无命中 Skill，按通用 AIOps 诊断流程规划。"
        )

        similar_incidents = find_similar_incidents(input_text, limit=3)
        incident_context = (
            "\n\n".join(
                f"- 任务: {incident['user_task']}\n"
                f"  Root cause summary: {incident['root_cause']}\n"
                f"  Tools: {', '.join(incident['tools_used'])}"
                for incident in similar_incidents
            )
            if similar_incidents
            else "无相似 Incident Case。"
        )

        alerts_trace_events: list[dict[str, Any]] = []
        active_alerts = list(state.get("active_alerts", []) or [])
        target_alert = state.get("target_alert")
        if should_fetch_active_alerts(mode, input_text):
            active_alert_tool = tool_map.get("get_active_alerts") or tool_map.get("list_active_alerts")
            if active_alert_tool is None:
                logger.warning("默认巡检模式未找到 get_active_alerts/list_active_alerts 工具")
            else:
                alert_result = await invoke_tool(active_alert_tool, {"include_resolved": False})
                active_alerts = list(
                    alert_result.get("active_alerts")
                    or alert_result.get("alerts")
                    or []
                )
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
                            metadata={
                                "active_alerts": active_alerts,
                                "target_alert": target_alert,
                            },
                        )
                    )
                    return {
                        "plan": plan_steps,
                        "active_alerts": active_alerts,
                        "target_alert": target_alert,
                        "similar_incidents": similar_incidents,
                        "trace_events": alerts_trace_events,
                    }

        # 步骤1: 查询内部文档获取相关经验
        logger.info("查询内部文档，寻找相关经验...")
        experience_docs = ""
        try:
            # retrieve_knowledge 使用 response_format="content_and_artifact"
            # ainvoke() 只返回 content（字符串），不是元组
            context_str = await retrieve_knowledge.ainvoke({"query": input_text})
            if context_str and context_str.strip():
                experience_docs = context_str
                logger.info(f"找到相关经验文档，长度: {len(experience_docs)}")
            else:
                logger.info("未找到相关经验文档")
        except Exception as e:
            logger.warning(f"查询内部文档失败: {e}")

        logger.info(f"可用工具数量: 本地 {len(local_tools)} + MCP {len(mcp_tools)}")

        # 格式化工具描述
        tools_description = format_tools_description(all_tools)

        # 步骤3: 格式化经验文档上下文
        if experience_docs:
            experience_context = dedent(f"""
                ## 相关经验文档

                以下是从知识库中检索到的相关经验和最佳实践，请参考这些经验制定执行计划：

                {experience_docs}

                ---
            """).strip()
        else:
            experience_context = ""

        # 步骤4: 创建 LLM 并生成计划
        llm = ChatQwen(
            model=config.rag_model,
            api_key=config.dashscope_api_key,
            temperature=0
        )

        planner_chain = planner_prompt | llm.with_structured_output(Plan)

        # 调用 LLM 生成计划
        plan_result = await planner_chain.ainvoke({
            "messages": [("user", input_text)],
            "tools_description": tools_description,
            "experience_context": (
                dedent(
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

                    {experience_context}
                    """
                ).strip()
            ),
        })

        # 提取步骤列表
        if isinstance(plan_result, Plan):
            plan_steps = plan_result.steps
        else:
            # 如果返回的是字典，提取 steps 字段
            plan_steps = plan_result.get("steps", [])  # type: ignore

        logger.info(f"计划已生成，共 {len(plan_steps)} 个步骤")
        for i, step in enumerate(plan_steps, 1):
            logger.info(f"  步骤{i}: {step}")

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

    except Exception as e:
        logger.error(f"生成计划失败: {e}", exc_info=True)
        # 返回一个默认计划
        return {
            "plan": ["收集相关信息", "分析数据", "生成报告"],
            "trace_events": [
                create_trace_event(
                    session_id=session_id,
                    node="planner",
                    status="error",
                    title="Planner fallback",
                    result_summary=str(e),
                )
            ],
        }
