"""Agent governance and skill draft APIs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from app.agent.aiops.runtime_store import runtime_store
from app.agent.aiops.skill_draft_generator import (
    delete_skill_draft,
    enable_skill_draft,
    list_skill_drafts,
)
from app.models.agent import AgentActionRequest


router = APIRouter(prefix="/api/agent")


@router.get("/pending-actions/{session_id}")
async def get_pending_actions(session_id: str):
    """Return pending approval actions for a session."""
    payload = runtime_store.load_pending_actions(session_id)
    return JSONResponse(status_code=200, content=payload)


@router.post("/approve")
async def approve_action(request: AgentActionRequest):
    """Approve a dangerous action and allow workflow resume."""
    payload = runtime_store.update_pending_action(
        session_id=request.session_id,
        action_id=request.action_id,
        status="approved",
        operator=request.operator,
        comment=request.comment,
    )
    snapshot = runtime_store.load_session(request.session_id)
    if snapshot and snapshot.get("state", {}).get("pending_action"):
        snapshot["state"]["pending_action"]["status"] = "approved"
        runtime_store.save_session(request.session_id, snapshot["state"], "running")

    return {
        "code": 200,
        "message": "success",
        "data": {
            "session_id": request.session_id,
            "action_id": request.action_id,
            "status": "approved",
            "pending_actions": payload.get("actions", []),
        },
    }


@router.post("/reject")
async def reject_action(request: AgentActionRequest):
    """Reject a dangerous action and allow workflow resume."""
    payload = runtime_store.update_pending_action(
        session_id=request.session_id,
        action_id=request.action_id,
        status="rejected",
        operator=request.operator,
        comment=request.comment,
    )
    snapshot = runtime_store.load_session(request.session_id)
    if snapshot and snapshot.get("state", {}).get("pending_action"):
        snapshot["state"]["pending_action"]["status"] = "rejected"
        runtime_store.save_session(request.session_id, snapshot["state"], "running")

    return {
        "code": 200,
        "message": "success",
        "data": {
            "session_id": request.session_id,
            "action_id": request.action_id,
            "status": "rejected",
            "pending_actions": payload.get("actions", []),
        },
    }


@router.get("/skill-drafts")
async def get_skill_drafts():
    """List all draft skills."""
    return {
        "code": 200,
        "message": "success",
        "data": list_skill_drafts(),
    }


@router.post("/skill-drafts/{draft_name}/enable")
async def enable_draft(draft_name: str):
    """Enable one draft skill."""
    try:
        path = enable_skill_draft(draft_name)
        return {
            "code": 200,
            "message": "success",
            "data": {"name": draft_name, "path": path},
        }
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(f"启用 Skill draft 失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.delete("/skill-drafts/{draft_name}")
async def remove_draft(draft_name: str):
    """Delete one draft skill."""
    try:
        delete_skill_draft(draft_name)
        return {
            "code": 200,
            "message": "success",
            "data": {"name": draft_name},
        }
    except Exception as exc:
        logger.error(f"删除 Skill draft 失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
