"""Chat API routes for standard RAG conversations."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse
from loguru import logger

from app.models.request import ChatRequest, ClearRequest
from app.models.response import ApiResponse, SessionInfoResponse
from app.services.rag_agent_service import rag_agent_service


router = APIRouter()


@router.post("/chat")
async def chat(request: ChatRequest):
    """Standard non-streaming RAG chat endpoint."""
    try:
        logger.info("[session {}] chat request: {}", request.id, request.question)
        answer = await rag_agent_service.query(
            request.question,
            session_id=request.id,
        )
        return {
            "code": 200,
            "message": "success",
            "data": {
                "success": True,
                "answer": answer,
                "errorMessage": None,
            },
        }
    except Exception as exc:
        logger.error("Chat request failed: {}", exc)
        return {
            "code": 500,
            "message": "error",
            "data": {
                "success": False,
                "answer": None,
                "errorMessage": str(exc),
            },
        }


@router.post("/chat_stream")
async def chat_stream(request: ChatRequest):
    """Streaming RAG chat endpoint using SSE."""
    logger.info("[session {}] chat_stream request: {}", request.id, request.question)

    async def event_generator():
        try:
            async for chunk in rag_agent_service.query_stream(request.question, session_id=request.id):
                chunk_type = chunk.get("type", "unknown")
                chunk_data = chunk.get("data")

                if chunk_type == "debug":
                    payload = {
                        "type": "debug",
                        "node": chunk.get("node", "unknown"),
                        "message_type": chunk.get("message_type", "unknown"),
                    }
                elif chunk_type == "tool_call":
                    payload = {"type": "tool_call", "data": chunk_data}
                elif chunk_type == "search_results":
                    payload = {"type": "search_results", "data": chunk_data}
                elif chunk_type == "content":
                    payload = {
                        "type": "content",
                        "data": chunk_data,
                        "content": chunk_data,
                    }
                elif chunk_type == "complete":
                    answer = (chunk_data or {}).get("answer", "") if isinstance(chunk_data, dict) else ""
                    payload = {
                        "type": "done",
                        "data": chunk_data,
                        "answer": answer,
                    }
                elif chunk_type == "error":
                    payload = {
                        "type": "error",
                        "data": str(chunk_data),
                        "message": str(chunk_data),
                    }
                else:
                    payload = {"type": chunk_type, "data": chunk_data}

                yield {
                    "event": "message",
                    "data": json.dumps(payload, ensure_ascii=False),
                }

            logger.info("[session {}] chat_stream completed", request.id)

        except Exception as exc:
            logger.error("chat_stream failed: {}", exc)
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "error",
                        "data": str(exc),
                        "message": str(exc),
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_generator())


@router.post("/chat/clear", response_model=ApiResponse)
async def clear_session(request: ClearRequest):
    """Clear one chat session."""
    try:
        success = rag_agent_service.clear_session(request.session_id)
        return ApiResponse(
            status="success" if success else "error",
            message="会话已清空" if success else "清空会话失败",
            data=None,
        )
    except Exception as exc:
        logger.error("clear_session failed: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/chat/session/{session_id}", response_model=SessionInfoResponse)
async def get_session_info(session_id: str) -> SessionInfoResponse:
    """Return one chat session history."""
    try:
        history = rag_agent_service.get_session_history(session_id)
        return SessionInfoResponse(
            session_id=session_id,
            message_count=len(history),
            history=history,
        )
    except Exception as exc:
        logger.error("get_session_info failed: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
