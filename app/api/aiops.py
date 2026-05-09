"""AIOps streaming API."""

from __future__ import annotations

import json

from fastapi import APIRouter
from loguru import logger
from sse_starlette.sse import EventSourceResponse

from app.models.aiops import AIOpsRequest
from app.services.aiops_service import DEFAULT_AIOPS_TASK, aiops_service


router = APIRouter()


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
