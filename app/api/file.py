"""File upload and indexing APIs."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from loguru import logger

from app.services.vector_index_service import vector_index_service


router = APIRouter()

UPLOAD_DIR = Path("./uploads")
ALLOWED_EXTENSIONS = ["txt", "md", "markdown"]
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file and try to index it immediately."""
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="文件名不能为空")

        safe_filename = _sanitize_filename(file.filename)
        file_extension = _get_file_extension(safe_filename)
        if file_extension not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"仅支持以下文件类型: {', '.join(ALLOWED_EXTENSIONS)}",
            )

        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        file_path = UPLOAD_DIR / safe_filename
        if file_path.exists():
            logger.info(f"Uploaded file already exists, replacing: {file_path}")
            file_path.unlink()

        content = await file.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="文件大小不能超过 10MB")

        file_path.write_bytes(content)
        logger.info(f"File uploaded: {file_path}")

        indexed = True
        index_error = None
        try:
            vector_index_service.index_single_file(str(file_path))
            logger.info(f"File indexed: {file_path}")
        except Exception as exc:  # pragma: no cover
            indexed = False
            index_error = str(exc)
            logger.error(f"File index failed: {file_path}, error: {exc}")

        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success" if indexed else "partial_success",
                "data": {
                    "filename": safe_filename,
                    "file_path": str(file_path),
                    "size": len(content),
                    "indexed": indexed,
                    "index_error": index_error,
                },
            },
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"File upload failed: {exc}")
        raise HTTPException(status_code=500, detail=f"文件上传失败: {exc}") from exc


@router.post("/index_directory")
async def index_directory(directory_path: str | None = None):
    """Index a directory and return the aggregated result."""
    try:
        logger.info(f"Indexing directory: {directory_path or 'uploads'}")
        result = vector_index_service.index_directory(directory_path)
        return JSONResponse(
            status_code=200,
            content={
                "code": 200,
                "message": "success" if result.success else "partial_success",
                "data": result.to_dict(),
            },
        )
    except Exception as exc:
        logger.error(f"Directory indexing failed: {exc}")
        raise HTTPException(status_code=500, detail=f"目录索引失败: {exc}") from exc


def _get_file_extension(filename: str) -> str:
    parts = filename.rsplit(".", 1)
    return parts[1].lower() if len(parts) == 2 else ""


def _sanitize_filename(filename: str) -> str:
    sanitized = filename.replace(" ", "_")
    for char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
        sanitized = sanitized.replace(char, "_")
    return sanitized
