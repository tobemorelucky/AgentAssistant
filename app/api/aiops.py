"""AIOps streaming API."""

from __future__ import annotations

import json

from fastapi import APIRouter
from fastapi import HTTPException
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from app.agent.aiops.heartbeat import heartbeat_manager
from app.agent.aiops.memory.session_memory import clear_session_memory, load_session_memory
from app.agent.aiops.remediation import evaluate_action_policy
from app.agent.aiops.runtime_store import runtime_store
from app.models.agent import HeartbeatRunRequest, RemediationDryRunRequest, RemediationExecuteRequest
from app.models.aiops import AIOpsRequest
from app.monitoring.monitor_provider import dry_run_remediation_action, execute_remediation_action
from app.services.aiops_service import DEFAULT_AIOPS_TASK, aiops_service
from app.config import config


router = APIRouter()


def _session_memory_debug_api_enabled() -> bool:
    return bool(config.debug or getattr(config, "aiops_session_memory_debug_api", False))


def _ensure_session_memory_debug_api_enabled() -> None:
    if not _session_memory_debug_api_enabled():
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/aiops")
async def diagnose_stream(request: AIOpsRequest):
    """Stream AIOps diagnosis events over SSE."""
    session_id = request.session_id or "default"
    mode = (request.mode or "default").strip().lower() or "default"
    task = (request.task or "").strip() or DEFAULT_AIOPS_TASK
    logger.info(f"[会话 {session_id}] 收到 AIOps 诊断请求（流式），mode={mode}, task={task}")

    async def event_generator():
        try:
            async for event in aiops_service.diagnose(session_id=session_id, task=task, mode=mode):
                safe_event = event if isinstance(event, dict) else {"type": "error", "message": str(event)}
                yield {
                    "event": "message",
                    "data": json.dumps(safe_event, ensure_ascii=False, default=str),
                }
                if safe_event.get("type") in {"complete", "error"}:
                    break

            logger.info(f"[会话 {session_id}] AIOps 诊断流式响应完成")

        except Exception as exc:
            logger.error(f"[会话 {session_id}] AIOps 诊断流式响应异常: {exc}", exc_info=True)
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "error",
                        "stage": "exception",
                        "message": f"诊断异常: {str(exc)}",
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            }

    return EventSourceResponse(event_generator())


@router.post("/v1/aiops/heartbeat/run")
async def run_heartbeat(request: HeartbeatRunRequest):
    record = await heartbeat_manager.run_once(trigger=request.trigger or "manual", session_id=request.session_id)
    return {"code": 200, "message": "success", "data": record}


@router.get("/v1/aiops/heartbeat/latest")
async def latest_heartbeat():
    return {"code": 200, "message": "success", "data": heartbeat_manager.latest()}


@router.get("/v1/aiops/heartbeat/history")
async def heartbeat_history(limit: int = 20):
    return {"code": 200, "message": "success", "data": heartbeat_manager.history(limit=limit)}


@router.post("/v1/aiops/remediation/dry-run")
async def remediation_dry_run(request: RemediationDryRunRequest):
    result = dry_run_remediation_action(request.action_id, request.params)
    runtime_store.append_audit_event(
        "remediation_dry_run",
        {
            "session_id": request.session_id,
            "action_id": request.action_id,
            "ok": result.get("ok"),
        },
    )
    if result.get("ok") is False:
        raise HTTPException(status_code=400, detail=result)
    return {"code": 200, "message": "success", "data": result}


@router.post("/v1/aiops/remediation/execute")
async def remediation_execute(request: RemediationExecuteRequest):
    policy = evaluate_action_policy(request.action_id, approval_token=request.approval_token)
    runtime_store.append_audit_event(
        "remediation_execute_requested",
        {
            "session_id": request.session_id,
            "action_id": request.action_id,
            "operator": request.operator,
            "decision": policy.get("decision"),
        },
    )
    if not bool(policy.get("allowed")):
        raise HTTPException(status_code=403, detail=policy)

    result = execute_remediation_action(
        request.dry_run_id,
        request.action_id,
        request.approval_token,
        request.operator,
        request.reason,
    )
    runtime_store.append_audit_event(
        "remediation_execute_result",
        {
            "session_id": request.session_id,
            "action_id": request.action_id,
            "operator": request.operator,
            "ok": result.get("ok"),
        },
    )
    if result.get("ok") is False:
        raise HTTPException(status_code=400, detail=result)
    return {"code": 200, "message": "success", "data": result}


@router.get("/v1/aiops/session-memory/{session_id}")
async def get_session_memory_debug(session_id: str):
    _ensure_session_memory_debug_api_enabled()
    memory = load_session_memory(session_id)
    return {"code": 200, "message": "success", "data": memory}


@router.delete("/v1/aiops/session-memory/{session_id}")
async def delete_session_memory_debug(session_id: str):
    _ensure_session_memory_debug_api_enabled()
    memory = clear_session_memory(session_id)
    return {"code": 200, "message": "success", "data": memory}
