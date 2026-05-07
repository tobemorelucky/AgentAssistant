"""
AIOps Agent orchestration service.
"""

from __future__ import annotations

from copy import deepcopy
from textwrap import dedent
from typing import Any, AsyncGenerator

from langgraph.graph import END, START, StateGraph
from loguru import logger

from app.agent.aiops import (
    PlanExecuteState,
    executor,
    planner,
    replanner,
    skill_router,
    verifier,
)
from app.agent.aiops.incident_memory import append_incident, build_incident_record
from app.agent.aiops.runtime_store import runtime_store
from app.agent.aiops.skill_draft_generator import generate_skill_draft
from app.agent.aiops.trace import append_trace_event, create_trace_event


NODE_SKILL_ROUTER = "skill_router"
NODE_PLANNER = "planner"
NODE_EXECUTOR = "executor"
NODE_REPLANNER = "replanner"
NODE_VERIFIER = "verifier"
APPEND_FIELDS = {"past_steps", "trace_events", "tools_used"}


class AIOpsService:
    """Governed AIOps Agent platform service."""

    def __init__(self) -> None:
        self.graph = self._build_graph()
        logger.info("Governed AIOps Agent Service 初始化完成")

    def _build_graph(self):
        logger.info("构建 Agent 工作流图...")
        workflow = StateGraph(PlanExecuteState)
        workflow.add_node(NODE_SKILL_ROUTER, skill_router)
        workflow.add_node(NODE_PLANNER, planner)
        workflow.add_node(NODE_EXECUTOR, executor)
        workflow.add_node(NODE_REPLANNER, replanner)
        workflow.add_node(NODE_VERIFIER, verifier)

        workflow.add_conditional_edges(
            START,
            self._select_entry_node,
            {
                NODE_SKILL_ROUTER: NODE_SKILL_ROUTER,
                NODE_PLANNER: NODE_PLANNER,
                NODE_EXECUTOR: NODE_EXECUTOR,
                NODE_REPLANNER: NODE_REPLANNER,
            },
        )
        workflow.add_edge(NODE_SKILL_ROUTER, NODE_PLANNER)
        workflow.add_edge(NODE_PLANNER, NODE_EXECUTOR)
        workflow.add_conditional_edges(
            NODE_EXECUTOR,
            self._after_executor,
            {
                NODE_REPLANNER: NODE_REPLANNER,
                END: END,
            },
        )
        workflow.add_conditional_edges(
            NODE_REPLANNER,
            self._after_replanner,
            {
                NODE_EXECUTOR: NODE_EXECUTOR,
                NODE_VERIFIER: NODE_VERIFIER,
                END: END,
            },
        )
        workflow.add_conditional_edges(
            NODE_VERIFIER,
            self._after_verifier,
            {
                NODE_REPLANNER: NODE_REPLANNER,
                END: END,
            },
        )
        return workflow.compile()

    @staticmethod
    def _select_entry_node(state: PlanExecuteState) -> str:
        return state.get("entry_node", NODE_SKILL_ROUTER)

    @staticmethod
    def _after_executor(state: PlanExecuteState) -> str:
        if state.get("status") == "paused" or state.get("pending_action"):
            return END
        return NODE_REPLANNER

    @staticmethod
    def _after_replanner(state: PlanExecuteState) -> str:
        if state.get("status") == "paused":
            return END
        if state.get("response"):
            return NODE_VERIFIER
        if state.get("plan"):
            return NODE_EXECUTOR
        return END

    @staticmethod
    def _after_verifier(state: PlanExecuteState) -> str:
        verifier_result = state.get("verifier_result", {})
        if verifier_result and not verifier_result.get("passed", True) and state.get("plan"):
            return NODE_REPLANNER
        return END

    @staticmethod
    def _merge_state(current_state: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
        merged = deepcopy(current_state)
        for key, value in updates.items():
            if key in APPEND_FIELDS:
                merged.setdefault(key, [])
                merged[key].extend(value)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _build_aiops_task() -> str:
        return dedent(
            """诊断当前系统是否存在告警，如果存在告警请详细分析告警原因并生成诊断报告，诊断报告输出格式要求：
            ```
            # 告警分析报告

            ## 活跃告警清单
            - 告警名称、级别、目标服务、时间窗口、状态

            ## 执行轨迹摘要
            - 规划步骤
            - 工具调用摘要
            - 审批与策略阻断情况

            ## 根因分析
            - 症状描述
            - 根因结论

            ## 关键证据
            - 指标证据
            - 日志证据
            - 历史案例证据

            ## 影响范围
            - 受影响服务
            - 时间范围
            - 风险范围

            ## 风险提示
            - 高风险建议与注意事项

            ## 处理建议
            - 处理步骤
            - 后续建议
            ```

            重要提醒：
            - 最终输出必须是纯 Markdown 文本，不要包含 JSON 结构
            - 所有内容必须基于工具查询的真实数据，严禁编造
            - 如果某个步骤失败，在结论中如实说明，不要跳过"""
        ).strip()

    def _build_initial_state(self, user_input: str, session_id: str) -> dict[str, Any]:
        snapshot = runtime_store.load_session(session_id)
        pending_payload = runtime_store.load_pending_actions(session_id)
        pending_status = pending_payload.get("status")

        if snapshot and snapshot.get("state"):
            state = snapshot["state"]
            state["session_id"] = session_id
            if pending_status in {"approved", "rejected"} and state.get("pending_action"):
                state["pending_action"]["status"] = pending_status
                state["entry_node"] = NODE_EXECUTOR
                state["status"] = "running"
                logger.info(f"[会话 {session_id}] 从审批结果恢复执行: {pending_status}")
                return state
            if pending_status == "pending" and state.get("pending_action"):
                state["entry_node"] = NODE_EXECUTOR
                state["status"] = "paused"
                logger.info(f"[会话 {session_id}] 会话仍在等待审批")
                return state
            if snapshot.get("status") == "running":
                state["entry_node"] = NODE_REPLANNER if state.get("plan") else NODE_SKILL_ROUTER
                return state

        return {
            "session_id": session_id,
            "input": user_input,
            "entry_node": NODE_SKILL_ROUTER,
            "status": "running",
            "plan": [],
            "past_steps": [],
            "response": "",
            "matched_skills": [],
            "similar_incidents": [],
            "trace_events": [],
            "tools_used": [],
            "verifier_result": {},
            "pending_action": None,
        }

    @staticmethod
    def _status_event(message: str, stage: str) -> dict[str, Any]:
        return {"type": "status", "stage": stage, "message": message}

    def _format_node_events(
        self,
        node_name: str,
        node_output: dict[str, Any] | None,
        current_state: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not node_output:
            return []

        events: list[dict[str, Any]] = []
        if node_name == NODE_SKILL_ROUTER:
            matched = current_state.get("matched_skills", [])
            message = "未匹配到 Runbook Skill，使用通用诊断流程。"
            if matched:
                message = f"命中 {len(matched)} 个 Skill: {', '.join(skill['name'] for skill in matched)}"
            events.append(self._status_event(message, NODE_SKILL_ROUTER))

        elif node_name == NODE_PLANNER:
            plan = current_state.get("plan", [])
            events.append(
                {
                    "type": "plan",
                    "stage": "plan_created",
                    "message": f"执行计划已制定，共 {len(plan)} 个步骤",
                    "plan": plan,
                    "skills": [skill.get("name") for skill in current_state.get("matched_skills", [])],
                }
            )

        elif node_name == NODE_EXECUTOR:
            pending_action = node_output.get("pending_action")
            if pending_action:
                events.append(
                    {
                        "type": "approval_required",
                        "stage": "approval_required",
                        "action_id": pending_action.get("action_id"),
                        "tool_name": pending_action.get("tool_name"),
                        "tool_args_summary": pending_action.get("tool_args_summary", ""),
                        "reason": pending_action.get("reason", ""),
                    }
                )
            else:
                past_steps = current_state.get("past_steps", [])
                if past_steps:
                    last_step, result = past_steps[-1]
                    events.append(
                        {
                            "type": "step_complete",
                            "stage": "step_executed",
                            "message": f"步骤执行完成 ({len(past_steps)}/{len(past_steps) + len(current_state.get('plan', []))})",
                            "current_step": last_step,
                            "remaining_steps": len(current_state.get("plan", [])),
                            "result_preview": str(result)[:200],
                        }
                    )

        elif node_name == NODE_REPLANNER:
            if node_output.get("response"):
                events.append(
                    {
                        "type": "report",
                        "stage": "final_report",
                        "message": "最终报告已生成",
                        "report": node_output.get("response", ""),
                    }
                )
            else:
                events.append(
                    self._status_event(
                        f"评估完成，剩余步骤 {len(current_state.get('plan', []))}",
                        NODE_REPLANNER,
                    )
                )

        elif node_name == NODE_VERIFIER:
            verifier_result = current_state.get("verifier_result", {})
            if verifier_result:
                events.append(
                    {
                        "type": "verifier_result",
                        "stage": "verifier",
                        "passed": verifier_result.get("passed", False),
                        "findings": verifier_result.get("findings", []),
                        "suggested_next_steps": verifier_result.get("suggested_next_steps", []),
                    }
                )

        return events

    async def execute(
        self,
        user_input: str,
        session_id: str = "default",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute the governed Agent workflow."""
        logger.info(f"[会话 {session_id}] 开始执行任务")
        current_state = self._build_initial_state(user_input, session_id)

        if current_state.get("status") == "paused" and current_state.get("pending_action"):
            pending_action = current_state["pending_action"]
            yield {
                "type": "approval_required",
                "stage": "approval_required",
                "action_id": pending_action.get("action_id"),
                "tool_name": pending_action.get("tool_name"),
                "tool_args_summary": pending_action.get("tool_args_summary", ""),
                "reason": pending_action.get("reason", ""),
            }
            return

        try:
            async for event in self.graph.astream(input=current_state, stream_mode="updates"):
                for node_name, node_output in event.items():
                    logger.info(f"节点 '{node_name}' 输出事件")
                    if not isinstance(node_output, dict):
                        continue

                    current_state = self._merge_state(current_state, node_output)
                    runtime_store.save_session(
                        session_id,
                        current_state,
                        current_state.get("status", "running"),
                    )

                    for trace_event in node_output.get("trace_events", []):
                        append_trace_event(session_id, trace_event)
                        yield {
                            "type": "trace",
                            "stage": node_name,
                            "trace": trace_event,
                        }

                    if node_output.get("pending_action"):
                        runtime_store.save_pending_action(session_id, node_output["pending_action"])

                    for formatted_event in self._format_node_events(node_name, node_output, current_state):
                        yield formatted_event

            if current_state.get("status") == "paused" and current_state.get("pending_action"):
                return

            runtime_store.clear_pending_actions(session_id)
            incident_record = build_incident_record(current_state)
            append_incident(incident_record)
            memory_trace = create_trace_event(
                session_id=session_id,
                node="memory",
                status="success",
                title="Incident memory saved",
                result_summary=incident_record.get("user_task", "")[:180],
            )
            append_trace_event(session_id, memory_trace)
            yield {"type": "trace", "stage": "memory", "trace": memory_trace}

            draft_path = generate_skill_draft(incident_record)
            draft_trace = create_trace_event(
                session_id=session_id,
                node="memory",
                status="success",
                title="Skill draft generated",
                result_summary=draft_path,
            )
            append_trace_event(session_id, draft_trace)
            yield {"type": "trace", "stage": "memory", "trace": draft_trace}

            runtime_store.save_session(session_id, current_state, "completed")
            yield {
                "type": "complete",
                "stage": "complete",
                "message": "任务执行完成",
                "response": current_state.get("response", ""),
            }

        except Exception as exc:
            logger.error(f"[会话 {session_id}] 任务执行失败: {exc}", exc_info=True)
            error_trace = create_trace_event(
                session_id=session_id,
                node="executor",
                status="error",
                title="Workflow failed",
                result_summary=str(exc),
            )
            append_trace_event(session_id, error_trace)
            yield {"type": "trace", "stage": "error", "trace": error_trace}
            yield {
                "type": "error",
                "stage": "error",
                "message": f"任务执行出错: {str(exc)}",
            }

    async def diagnose(self, session_id: str = "default") -> AsyncGenerator[dict[str, Any], None]:
        """Compatibility wrapper for the existing /api/aiops endpoint."""
        aiops_task = self._build_aiops_task()
        yield {
            "type": "status",
            "stage": "workflow_started",
            "message": "AIOps Agent 已接收诊断请求，正在初始化工作流。",
        }
        async for event in self.execute(aiops_task, session_id):
            if event.get("type") == "complete":
                yield {
                    "type": "complete",
                    "stage": "diagnosis_complete",
                    "message": "诊断流程完成",
                    "diagnosis": {
                        "status": "completed",
                        "report": event.get("response", ""),
                    },
                }
            else:
                yield event


aiops_service = AIOpsService()
