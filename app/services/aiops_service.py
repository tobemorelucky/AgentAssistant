"""AIOps Agent orchestration service."""

from __future__ import annotations

from copy import deepcopy
import json
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
from app.agent.aiops.disk_cleanup import summarize_disk_tool_result, unwrap_structured_payload
from app.agent.aiops.incident_memory import build_incident_record
from app.agent.aiops.patrol import summarize_structured_tool_result
from app.agent.aiops.runtime_store import runtime_store
from app.agent.aiops.trace import append_trace_event, create_trace_event


NODE_SKILL_ROUTER = "skill_router"
NODE_PLANNER = "planner"
NODE_EXECUTOR = "executor"
NODE_REPLANNER = "replanner"
NODE_VERIFIER = "verifier"
APPEND_FIELDS = {"past_steps", "trace_events", "tools_used"}
DEFAULT_AIOPS_TASK = (
    "请检查当前系统是否存在活跃告警。如果存在告警，请选择最高严重级别告警，"
    "结合监控指标、日志、历史工单和知识库 runbook 进行根因分析，并保留完整 Agent Trace。"
)


class AIOpsService:
    """Governed AIOps Agent platform service."""

    def __init__(self) -> None:
        self.graph = self._build_graph()
        logger.info("Governed AIOps Agent Service initialized")

    def _build_graph(self):
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
        workflow.add_conditional_edges(
            NODE_PLANNER,
            self._after_planner,
            {
                NODE_EXECUTOR: NODE_EXECUTOR,
                NODE_VERIFIER: NODE_VERIFIER,
                END: END,
            },
        )
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
    def _after_planner(state: PlanExecuteState) -> str:
        if state.get("status") == "paused":
            return END
        if state.get("response") and state.get("plan_source") in {
            "patrol_dispatch_no_tool",
            "patrol_dispatch_no_alert",
            "patrol_dispatch_unsupported_profile",
        }:
            return END
        if state.get("response"):
            return NODE_VERIFIER
        if state.get("plan"):
            return NODE_EXECUTOR
        return END

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
        stop_decision = state.get("stop_decision") or {}
        is_runtime_path = state.get("plan_source") == "investigation_runtime"
        if (
            verifier_result
            and not verifier_result.get("passed", True)
            and stop_decision.get("decision") in {"finalize", "finalize_with_limitations"}
            and is_runtime_path
        ):
            return END
        if verifier_result and not verifier_result.get("passed", True) and (state.get("plan") or is_runtime_path):
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
    def _skill_names(skills: list[Any]) -> list[str]:
        names: list[str] = []
        for skill in skills or []:
            if isinstance(skill, dict):
                name = skill.get("name")
            else:
                name = skill
            if name:
                names.append(str(name))
        return names

    @staticmethod
    def _summarize_step_result(result: Any) -> str:
        parsed = result
        if isinstance(result, str):
            text = result.strip()
            if not text:
                return ""
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text[:200]

        if isinstance(parsed, dict):
            if {"usage_percent", "used_gb", "available_gb"} <= set(parsed.keys()):
                return (
                    f"根分区使用率 {parsed.get('usage_percent')}%，"
                    f"已用 {parsed.get('used_gb')}GB，剩余 {parsed.get('available_gb')}GB"
                )
            if "directories" in parsed and isinstance(parsed["directories"], list):
                first = parsed["directories"][0] if parsed["directories"] else {}
                if isinstance(first, dict):
                    return f"Top 目录 {first.get('path', '-')} 占用 {first.get('size_gb', '-')}GB"
            if "files" in parsed and isinstance(parsed["files"], list):
                first = parsed["files"][0] if parsed["files"] else {}
                if isinstance(first, dict):
                    return f"Top 文件 {first.get('path', '-')} 占用 {first.get('size_gb', '-')}GB"
            if {"images_gb", "volumes_gb", "build_cache_gb"} & set(parsed.keys()):
                return (
                    f"Docker 占用 images {parsed.get('images_gb', '-')}GB, "
                    f"volumes {parsed.get('volumes_gb', '-')}GB, "
                    f"build cache {parsed.get('build_cache_gb', '-')}GB"
                )
            if {"safe", "need_approval", "forbidden"} & set(parsed.keys()):
                safe_count = len(parsed.get("safe", []) or [])
                approval_count = len(parsed.get("need_approval", []) or [])
                forbidden_count = len(parsed.get("forbidden", []) or [])
                return f"清理候选项：安全 {safe_count}，需确认 {approval_count}，禁止 {forbidden_count}"
            preferred_keys = ["message", "service_name", "alert_name", "status", "total"]
            fragments = [f"{key}={parsed[key]}" for key in preferred_keys if key in parsed]
            if fragments:
                return "，".join(fragments)[:200]
            filtered = {k: v for k, v in parsed.items() if k not in {"type", "test", "data"}}
            return json.dumps(filtered, ensure_ascii=False)[:200]

        if isinstance(parsed, list):
            if parsed and isinstance(parsed[0], dict):
                first = parsed[0]
                if isinstance(first, dict) and first.get("path"):
                    return f"共 {len(parsed)} 项，首项 {first.get('path')}"
            return f"共 {len(parsed)} 项结果"

        return str(parsed)[:200]

    @staticmethod
    def _summarize_step_result(result: Any) -> str:
        parsed = unwrap_structured_payload(result)

        if isinstance(parsed, str):
            text = parsed.strip()
            return text[:240] if text else ""

        if isinstance(parsed, dict):
            if "content" in parsed and "artifacts" in parsed:
                content = str(parsed.get("content", "")).strip()
                artifacts = parsed.get("artifacts", []) or []
                if artifacts and isinstance(artifacts[0], dict):
                    metadata = artifacts[0].get("metadata") or {}
                    if metadata.get("provider") == "tavily":
                        return summarize_structured_tool_result("web_search", parsed)[:240]
                return content[:240]
            if {"usage_percent", "used_gb", "total_gb", "available_gb"} & set(parsed.keys()):
                return summarize_disk_tool_result("get_disk_usage", parsed)[:240]
            if "directories" in parsed and isinstance(parsed["directories"], list):
                return summarize_disk_tool_result("list_large_directories", parsed)[:240]
            if "files" in parsed and isinstance(parsed["files"], list):
                first = parsed["files"][0] if parsed["files"] else {}
                if isinstance(first, dict) and ("process" in first or "process_name" in first):
                    return summarize_disk_tool_result("query_deleted_open_files", parsed)[:240]
                return summarize_disk_tool_result("list_large_files", parsed)[:240]
            if {"images_gb", "containers_gb", "volumes_gb", "build_cache_gb"} & set(parsed.keys()):
                return summarize_disk_tool_result("query_docker_disk_usage", parsed)[:240]
            if {"safe", "need_approval", "forbidden"} & set(parsed.keys()):
                return summarize_disk_tool_result("get_disk_cleanup_candidates", parsed)[:240]
            if parsed.get("error"):
                return f"执行失败: {parsed.get('error')}"[:240]
            if "statistics" in parsed and "service_name" in parsed:
                if "memory_usage" in parsed or "memory" in json.dumps(parsed, ensure_ascii=False).lower():
                    return summarize_structured_tool_result("query_memory_metrics", parsed)[:240]
                return summarize_structured_tool_result("query_cpu_metrics", parsed)[:240]
            if "processes" in parsed and isinstance(parsed["processes"], list):
                return summarize_structured_tool_result("query_process_list", parsed)[:240]
            if "tickets" in parsed and isinstance(parsed["tickets"], list):
                return summarize_structured_tool_result("search_historical_tickets", parsed)[:240]
            if "topics" in parsed and isinstance(parsed["topics"], list):
                return summarize_structured_tool_result("search_topic_by_service_name", parsed)[:240]
            if "logs" in parsed and isinstance(parsed["logs"], list):
                return summarize_structured_tool_result("search_log", parsed)[:240]
            if parsed.get("service_name") and ("owner_team" in parsed or "deployment" in parsed):
                return summarize_structured_tool_result("get_service_info", parsed)[:240]
            preferred_keys = ["message", "service_name", "alert_name", "status", "total"]
            fragments = [f"{key}={parsed[key]}" for key in preferred_keys if key in parsed]
            if fragments:
                return " | ".join(fragments)[:240]
            filtered = {k: v for k, v in parsed.items() if k not in {"type", "test", "data"}}
            return json.dumps(filtered, ensure_ascii=False)[:240]

        if isinstance(parsed, list):
            if parsed and isinstance(parsed[0], dict) and parsed[0].get("path"):
                preview = ", ".join(str(item.get("path")) for item in parsed[:3] if isinstance(item, dict))
                return f"结果包含 {len(parsed)} 项：{preview}"[:240]
            return f"结果包含 {len(parsed)} 项"[:240]

        return str(parsed)[:240]

    def _build_initial_state(self, user_input: str, session_id: str, mode: str) -> dict[str, Any]:
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
                return state
            if pending_status == "pending" and state.get("pending_action"):
                state["entry_node"] = NODE_EXECUTOR
                state["status"] = "paused"
                return state
            if snapshot.get("status") == "running":
                state["entry_node"] = NODE_REPLANNER if state.get("plan") else NODE_SKILL_ROUTER
                return state

        return {
            "session_id": session_id,
            "input": user_input,
            "mode": mode,
            "diagnosis_intent": "",
            "selected_profile": None,
            "evidence_store": {},
            "investigation_round": 0,
            "no_progress_rounds": 0,
            "last_investigation_slot": None,
            "stop_decision": None,
            "plan_source": "",
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
            "active_alerts": [],
            "target_alert": None,
            "incident_record": {},
            "feedback": {},
            "generated_skill_draft": None,
            "memory_persisted": False,
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
            message = "未命中额外 Runbook Skill，继续使用通用 AIOps 流程。"
            if matched:
                message = f"命中 {len(matched)} 个 Skill: {', '.join(self._skill_names(matched))}"
            events.append(self._status_event(message, NODE_SKILL_ROUTER))

        elif node_name == NODE_PLANNER:
            plan = current_state.get("plan", [])
            if current_state.get("response") and not plan:
                events.append(
                    {
                        "type": "report_draft",
                        "stage": "candidate_report",
                        "message": "候选报告已生成，等待 Verifier 校验",
                        "report": current_state.get("response", ""),
                    }
                )
            else:
                events.append(
                    {
                        "type": "plan",
                        "stage": "plan_created",
                        "message": f"诊断计划已生成，共 {len(plan)} 个步骤",
                        "plan": plan,
                        "skills": self._skill_names(current_state.get("matched_skills", [])),
                        "target_alert": current_state.get("target_alert"),
                        "active_alerts": current_state.get("active_alerts", []),
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
                            "result_preview": self._summarize_step_result(result),
                        }
                    )

        elif node_name == NODE_REPLANNER:
            if node_output.get("response"):
                events.append(
                    {
                        "type": "report_draft",
                        "stage": "candidate_report",
                        "message": "候选报告已生成，等待 Verifier 校验",
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
        mode: str = "default",
    ) -> AsyncGenerator[dict[str, Any], None]:
        current_state = self._build_initial_state(user_input, session_id, mode)

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
                        yield {"type": "trace", "stage": node_name, "trace": trace_event}

                    if node_output.get("pending_action"):
                        runtime_store.save_pending_action(session_id, node_output["pending_action"])

                    for formatted_event in self._format_node_events(node_name, node_output, current_state):
                        yield formatted_event

            if current_state.get("status") == "paused" and current_state.get("pending_action"):
                return

            runtime_store.clear_pending_actions(session_id)
            current_state["incident_record"] = build_incident_record(current_state)
            runtime_store.save_session(session_id, current_state, "completed")
            yield {
                "type": "complete",
                "stage": "complete",
                "message": "诊断完成",
                "response": current_state.get("response", ""),
            }

        except Exception as exc:
            logger.error(f"[session {session_id}] AIOps workflow failed: {exc}", exc_info=True)
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
                "message": f"诊断执行失败: {str(exc)}",
            }

    async def diagnose(
        self,
        session_id: str = "default",
        task: str | None = None,
        mode: str = "default",
    ) -> AsyncGenerator[dict[str, Any], None]:
        aiops_task = (task or DEFAULT_AIOPS_TASK).strip()
        yield {
            "type": "status",
            "stage": "workflow_started",
            "message": (
                "AIOps Agent 正在启动自定义诊断..."
                if mode == "custom"
                else "AIOps Agent 正在启动默认巡检..."
            ),
        }
        async for event in self.execute(aiops_task, session_id, mode=mode):
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
