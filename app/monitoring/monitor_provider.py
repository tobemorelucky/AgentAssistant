"""Provider abstraction for AIOps monitor data sources."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.config import config

try:
    import httpx
except ModuleNotFoundError:  # pragma: no cover - fallback for incomplete local envs
    class _HttpxFallback:
        class TimeoutException(Exception):
            pass

        class HTTPError(Exception):
            pass

        class HTTPStatusError(HTTPError):
            def __init__(self, *args: Any, response: Any = None, **kwargs: Any) -> None:
                super().__init__(*args)
                self.response = response

        Client = None

    httpx = _HttpxFallback()  # type: ignore[assignment]


logger = logging.getLogger("AIOpsMonitorProvider")

ROOT_DIR = Path(__file__).resolve().parents[2]
DISK_MOCK_PATH = ROOT_DIR / "mock_data" / "disk.json"
DEFAULT_REMOTE_TIMEOUT = 10.0


def get_monitor_provider_name() -> str:
    provider = (config.aiops_monitor_provider or "mock").strip().lower()
    return provider if provider in {"mock", "remote_host"} else "mock"


def get_remote_host_base_url() -> str:
    return (config.aiops_remote_host_base_url or "").strip().rstrip("/")


def get_remote_host_token() -> str:
    return (config.aiops_remote_host_token or "").strip()


def _log_provider_context(provider: str, tool_name: str) -> None:
    base_url = get_remote_host_base_url() if provider == "remote_host" else ""
    token_configured = bool(get_remote_host_token()) if provider == "remote_host" else False
    logger.info(
        "%s using provider=%s, remote_base_url=%s, remote_token_configured=%s",
        tool_name,
        provider,
        base_url or "-",
        token_configured,
    )


def _load_disk_mock_data() -> dict[str, Any]:
    with DISK_MOCK_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _error_result(
    *,
    tool_name: str,
    source: str,
    message: str,
    error_code: str,
    status_code: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "tool_name": tool_name,
        "source": source,
        "message": message,
        "error_code": error_code,
    }
    if status_code is not None:
        payload["status_code"] = status_code
    if extra:
        payload.update(extra)
    return payload


def _request_remote_json(path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
    base_url = get_remote_host_base_url()
    if not base_url:
        raise ValueError("AIOPS_REMOTE_HOST_BASE_URL 未配置")

    headers = {"Accept": "application/json"}
    token = get_remote_host_token()
    if token:
        headers["X-Host-Agent-Token"] = token

    url = f"{base_url}{path}"
    if httpx.Client is None:  # type: ignore[truthy-function]
        raise RuntimeError("httpx 未安装，无法请求远程 Host Agent")
    with httpx.Client(
        timeout=DEFAULT_REMOTE_TIMEOUT,
        follow_redirects=True,
        trust_env=True,
    ) as client:
        response = client.get(url, params=params or {}, headers=headers)
        response.raise_for_status()
        return response.status_code, response.json()


def _status_from_usage(usage_percent: Any) -> str:
    try:
        usage = float(usage_percent)
    except (TypeError, ValueError):
        return "unknown"
    if usage >= 90:
        return "critical"
    if usage >= 80:
        return "warning"
    return "healthy"


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _size_string_to_gb(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    number_match = None
    import re

    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*$", text)
    if not match:
        return _to_float(value)
    number_match = _to_float(match.group(1))
    unit = match.group(2).upper()
    if number_match is None:
        return None
    if unit in {"GB", "GIB"}:
        return round(number_match, 2)
    if unit in {"MB", "MIB"}:
        return round(number_match / 1024, 2)
    if unit in {"KB", "KIB"}:
        return round(number_match / (1024**2), 4)
    if unit in {"B", "BYTE", "BYTES"}:
        return round(number_match / (1024**3), 4)
    return None


def _directory_reason(path: str) -> str:
    mapping = {
        "/var/log": "业务日志与归档日志堆积",
        "/var/lib/docker": "Docker 镜像、卷或构建缓存占用",
        "/tmp": "临时文件未定期清理",
        "/app/cache": "应用缓存未过期或未淘汰",
    }
    return mapping.get(path, "目录占用偏高，需要进一步核查内容组成")


def _extract_directory_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("directories", "items", "results", "entries"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            return _extract_directory_items(data)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _extract_docker_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        if payload.get("ok") is False:
            return payload
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            normalized: dict[str, Any] = {}
            for item in data:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("type") or "").strip().lower()
                size_gb = _size_string_to_gb(item.get("size"))
                if item_type == "images":
                    normalized["images_gb"] = size_gb
                elif item_type == "containers":
                    normalized["containers_gb"] = size_gb
                elif item_type in {"local volumes", "volumes"}:
                    normalized["volumes_gb"] = size_gb
                elif item_type == "build cache":
                    normalized["build_cache_gb"] = size_gb
            if normalized:
                return normalized
        return payload
    return {}


def _extract_large_file_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        files = payload.get("files")
        if isinstance(files, list):
            return [item for item in files if isinstance(item, dict)]
        data = payload.get("data")
        if isinstance(data, dict):
            return _extract_large_file_items(data)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _extract_deleted_open_file_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        files = payload.get("files")
        if isinstance(files, list):
            return [item for item in files if isinstance(item, dict)]
        data = payload.get("data")
        if isinstance(data, dict):
            return _extract_deleted_open_file_items(data)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def get_disk_usage_data(hostname: str | None = None, mount: str = "/") -> dict[str, Any]:
    provider = get_monitor_provider_name()
    _log_provider_context(provider, "get_disk_usage")

    if provider == "mock":
        payload = _load_disk_mock_data()
        disk_usage = dict(payload.get("disk_usage", {}))
        if hostname:
            disk_usage["host"] = hostname
        disk_usage["mount"] = mount or disk_usage.get("mount", "/")
        disk_usage["status"] = disk_usage.get("status") or _status_from_usage(disk_usage.get("usage_percent"))
        disk_usage["source"] = "mock"
        return disk_usage

    try:
        status_code, payload = _request_remote_json("/api/v1/disk/usage", {"mount": mount or "/"})
    except ValueError as exc:
        logger.error("remote_host get_disk_usage config error: %s", exc)
        return _error_result(
            tool_name="get_disk_usage",
            source="remote_host",
            message=str(exc),
            error_code="remote_host_config_error",
            extra={"host": hostname, "mount": mount or "/"},
        )
    except httpx.TimeoutException as exc:
        logger.error("remote_host get_disk_usage timeout: %r", exc)
        return _error_result(
            tool_name="get_disk_usage",
            source="remote_host",
            message="请求 Host Agent 超时",
            error_code="remote_host_timeout",
            extra={"host": hostname, "mount": mount or "/"},
        )
    except httpx.HTTPStatusError as exc:
        logger.error("remote_host get_disk_usage http error: %s", exc)
        return _error_result(
            tool_name="get_disk_usage",
            source="remote_host",
            message=f"Host Agent 返回非 200 状态码: {exc.response.status_code}",
            error_code="remote_host_http_error",
            status_code=exc.response.status_code,
            extra={"host": hostname, "mount": mount or "/"},
        )
    except httpx.HTTPError as exc:
        logger.error("remote_host get_disk_usage request failed: %r", exc)
        return _error_result(
            tool_name="get_disk_usage",
            source="remote_host",
            message=f"请求 Host Agent 失败: {exc}",
            error_code="remote_host_request_error",
            extra={"host": hostname, "mount": mount or "/"},
        )
    except json.JSONDecodeError as exc:
        logger.error("remote_host get_disk_usage invalid json: %r", exc)
        return _error_result(
            tool_name="get_disk_usage",
            source="remote_host",
            message="Host Agent 返回了非法 JSON",
            error_code="remote_host_invalid_json",
            status_code=status_code if "status_code" in locals() else None,
            extra={"host": hostname, "mount": mount or "/"},
        )

    if not isinstance(payload, dict):
        logger.error("remote_host get_disk_usage invalid payload type: %s", type(payload).__name__)
        return _error_result(
            tool_name="get_disk_usage",
            source="remote_host",
            message="Host Agent 返回结构不符合预期",
            error_code="remote_host_invalid_payload",
            status_code=status_code,
            extra={"host": hostname, "mount": mount or "/"},
        )

    result = dict(payload)
    if hostname and not result.get("host"):
        result["host"] = hostname
    result["mount"] = result.get("mount") or mount or "/"
    result["status"] = result.get("status") or _status_from_usage(result.get("usage_percent"))
    result["source"] = "remote_host"
    return result


def list_large_directories_data(path: str = "/", limit: int = 10) -> dict[str, Any]:
    provider = get_monitor_provider_name()
    _log_provider_context(provider, "list_large_directories")

    if provider == "mock":
        payload = _load_disk_mock_data()
        directories = []
        for item in list(payload.get("large_directories", []) or [])[:limit]:
            directory = dict(item)
            directory["reason"] = directory.get("reason") or _directory_reason(str(directory.get("path", "")))
            directory["source"] = "mock"
            directories.append(directory)
        return {
            "path": path,
            "limit": limit,
            "directories": directories,
            "source": "mock",
        }

    try:
        status_code, payload = _request_remote_json(
            "/api/v1/disk/large-directories",
            {"path": path or "/", "limit": limit},
        )
    except ValueError as exc:
        logger.error("remote_host list_large_directories config error: %s", exc)
        return _error_result(
            tool_name="list_large_directories",
            source="remote_host",
            message=str(exc),
            error_code="remote_host_config_error",
            extra={"path": path or "/", "limit": limit},
        )
    except httpx.TimeoutException as exc:
        logger.error("remote_host list_large_directories timeout: %r", exc)
        return _error_result(
            tool_name="list_large_directories",
            source="remote_host",
            message="请求 Host Agent 超时",
            error_code="remote_host_timeout",
            extra={"path": path or "/", "limit": limit},
        )
    except httpx.HTTPStatusError as exc:
        logger.error("remote_host list_large_directories http error: %s", exc)
        return _error_result(
            tool_name="list_large_directories",
            source="remote_host",
            message=f"Host Agent 返回非 200 状态码: {exc.response.status_code}",
            error_code="remote_host_http_error",
            status_code=exc.response.status_code,
            extra={"path": path or "/", "limit": limit},
        )
    except httpx.HTTPError as exc:
        logger.error("remote_host list_large_directories request failed: %r", exc)
        return _error_result(
            tool_name="list_large_directories",
            source="remote_host",
            message=f"请求 Host Agent 失败: {exc}",
            error_code="remote_host_request_error",
            extra={"path": path or "/", "limit": limit},
        )
    except json.JSONDecodeError as exc:
        logger.error("remote_host list_large_directories invalid json: %r", exc)
        return _error_result(
            tool_name="list_large_directories",
            source="remote_host",
            message="Host Agent 返回了非法 JSON",
            error_code="remote_host_invalid_json",
            status_code=status_code if "status_code" in locals() else None,
            extra={"path": path or "/", "limit": limit},
        )

    directories = []
    for item in _extract_directory_items(payload)[:limit]:
        directory_path = str(item.get("path") or item.get("directory") or "")
        size_gb = _to_float(item.get("size_gb"))
        if size_gb is None:
            size_mb = _to_float(item.get("size_mb"))
            size_bytes = _to_float(item.get("size_bytes"))
            if size_mb is not None:
                size_gb = round(size_mb / 1024, 2)
            elif size_bytes is not None:
                size_gb = round(size_bytes / (1024**3), 2)
            else:
                size_gb = _size_string_to_gb(item.get("size_human"))
        directories.append(
            {
                "path": directory_path,
                "size_gb": size_gb,
                "reason": item.get("reason") or _directory_reason(directory_path),
                "source": "remote_host",
            }
        )

    if not directories and not isinstance(payload, (dict, list)):
        logger.error("remote_host list_large_directories invalid payload type: %s", type(payload).__name__)
        return _error_result(
            tool_name="list_large_directories",
            source="remote_host",
            message="Host Agent 返回结构不符合预期",
            error_code="remote_host_invalid_payload",
            status_code=status_code,
            extra={"path": path or "/", "limit": limit},
        )

    return {
        "path": path or "/",
        "limit": limit,
        "directories": directories,
        "source": "remote_host",
    }


def list_large_files_data(path: str = "/", min_size_mb: int = 100, limit: int = 20) -> dict[str, Any]:
    provider = get_monitor_provider_name()
    _log_provider_context(provider, "list_large_files")

    if provider == "mock":
        payload = _load_disk_mock_data()
        files = list(payload.get("large_files", []) or [])
        min_size_gb = round(min_size_mb / 1024, 3)
        filtered = []
        for item in files:
            size_gb = _to_float(item.get("size_gb"))
            if size_gb is None or size_gb < min_size_gb:
                continue
            filtered.append(
                {
                    "path": item.get("path"),
                    "size_gb": size_gb,
                    "size_mb": round(size_gb * 1024, 1),
                    "scan_root": path or "/",
                    "warning": item.get("warning") or "",
                    "source": "mock",
                }
            )
        return {
            "ok": True,
            "path": path or "/",
            "scan_root": path or "/",
            "min_size_mb": min_size_mb,
            "limit": limit,
            "files": filtered[:limit],
            "warnings": [],
            "scan_incomplete": False,
            "skipped_paths": [],
            "skipped_count": 0,
            "permission_denied_count": 0,
            "source": "mock",
        }

    try:
        status_code, payload = _request_remote_json(
            "/api/v1/disk/large-files",
            {"path": path or "/", "min_size_mb": min_size_mb, "limit": limit},
        )
    except ValueError as exc:
        logger.error("remote_host list_large_files config error: %s", exc)
        return _error_result(
            tool_name="list_large_files",
            source="remote_host",
            message=str(exc),
            error_code="remote_host_config_error",
            extra={"path": path or "/", "min_size_mb": min_size_mb, "limit": limit},
        )
    except httpx.TimeoutException as exc:
        logger.error("remote_host list_large_files timeout: %r", exc)
        return _error_result(
            tool_name="list_large_files",
            source="remote_host",
            message="请求 Host Agent 超时",
            error_code="remote_host_timeout",
            extra={"path": path or "/", "min_size_mb": min_size_mb, "limit": limit},
        )
    except httpx.HTTPStatusError as exc:
        logger.error("remote_host list_large_files http error: %s", exc)
        return _error_result(
            tool_name="list_large_files",
            source="remote_host",
            message=f"Host Agent 返回非 200 状态码: {exc.response.status_code}",
            error_code="remote_host_http_error",
            status_code=exc.response.status_code,
            extra={"path": path or "/", "min_size_mb": min_size_mb, "limit": limit},
        )
    except httpx.HTTPError as exc:
        logger.error("remote_host list_large_files request failed: %r", exc)
        return _error_result(
            tool_name="list_large_files",
            source="remote_host",
            message=f"请求 Host Agent 失败: {exc}",
            error_code="remote_host_request_error",
            extra={"path": path or "/", "min_size_mb": min_size_mb, "limit": limit},
        )
    except json.JSONDecodeError as exc:
        logger.error("remote_host list_large_files invalid json: %r", exc)
        return _error_result(
            tool_name="list_large_files",
            source="remote_host",
            message="Host Agent 返回了非法 JSON",
            error_code="remote_host_invalid_json",
            status_code=status_code if "status_code" in locals() else None,
            extra={"path": path or "/", "min_size_mb": min_size_mb, "limit": limit},
        )

    if not isinstance(payload, dict):
        logger.error("remote_host list_large_files invalid payload type: %s", type(payload).__name__)
        return _error_result(
            tool_name="list_large_files",
            source="remote_host",
            message="Host Agent 返回结构不符合预期",
            error_code="remote_host_invalid_payload",
            status_code=status_code,
            extra={"path": path or "/", "min_size_mb": min_size_mb, "limit": limit},
        )

    if payload.get("ok") is False:
        logger.error("remote_host list_large_files returned structured error: %s", payload)
        return _error_result(
            tool_name="list_large_files",
            source="remote_host",
            message=str(payload.get("message") or "Host Agent large-files 接口返回错误"),
            error_code=str(payload.get("error_code") or "remote_host_large_files_error"),
            status_code=status_code,
            extra={
                "path": path or "/",
                "min_size_mb": min_size_mb,
                "limit": limit,
                "warnings": payload.get("warnings") or [],
            },
        )

    normalized_files = []
    for item in _extract_large_file_items(payload)[:limit]:
        size_gb = _to_float(item.get("size_gb"))
        size_mb = _to_float(item.get("size_mb"))
        if size_gb is None and size_mb is not None:
            size_gb = round(size_mb / 1024, 2)
        if size_mb is None and size_gb is not None:
            size_mb = round(size_gb * 1024, 1)
        normalized_files.append(
            {
                "path": item.get("path"),
                "size_gb": size_gb,
                "size_mb": size_mb,
                "scan_root": item.get("scan_root") or payload.get("scan_root") or path or "/",
                "warning": item.get("warning") or "",
                "source": "remote_host",
            }
        )

    return {
        "ok": True,
        "path": path or "/",
        "scan_root": payload.get("scan_root") or path or "/",
        "min_size_mb": int(payload.get("min_size_mb") or min_size_mb),
        "limit": int(payload.get("limit") or limit),
        "files": normalized_files,
        "warnings": payload.get("warnings") or [],
        "scan_incomplete": bool(payload.get("scan_incomplete")),
        "skipped_paths": payload.get("skipped_paths") or [],
        "skipped_count": int(payload.get("skipped_count") or 0),
        "permission_denied_count": int(payload.get("permission_denied_count") or 0),
        "source": "remote_host",
    }


def query_deleted_open_files_data() -> dict[str, Any]:
    provider = get_monitor_provider_name()
    _log_provider_context(provider, "query_deleted_open_files")

    if provider == "mock":
        payload = _load_disk_mock_data()
        files = []
        for item in list(payload.get("deleted_open_files", []) or []):
            file_item = dict(item)
            process_name = file_item.get("process") or file_item.get("process_name") or ""
            file_item["process"] = process_name
            file_item["file"] = file_item.get("file") or file_item.get("path")
            file_item["suggestion"] = file_item.get("suggestion") or (
                f"在业务低峰平滑重启 {process_name}，释放已删除但未归还的磁盘空间"
                if process_name
                else "确认句柄所属进程后再安排平滑重启释放空间"
            )
            file_item["source"] = "mock"
            files.append(file_item)
        return {
            "ok": True,
            "files": files,
            "total": len(files),
            "source": "mock",
        }

    try:
        status_code, payload = _request_remote_json("/api/v1/disk/deleted-open-files")
    except ValueError as exc:
        logger.error("remote_host query_deleted_open_files config error: %s", exc)
        return _error_result(
            tool_name="query_deleted_open_files",
            source="remote_host",
            message=str(exc),
            error_code="remote_host_config_error",
        )
    except httpx.TimeoutException as exc:
        logger.error("remote_host query_deleted_open_files timeout: %r", exc)
        return _error_result(
            tool_name="query_deleted_open_files",
            source="remote_host",
            message="请求 Host Agent 超时",
            error_code="remote_host_timeout",
        )
    except httpx.HTTPStatusError as exc:
        logger.error("remote_host query_deleted_open_files http error: %s", exc)
        return _error_result(
            tool_name="query_deleted_open_files",
            source="remote_host",
            message=f"Host Agent 返回非 200 状态码: {exc.response.status_code}",
            error_code="remote_host_http_error",
            status_code=exc.response.status_code,
        )
    except httpx.HTTPError as exc:
        logger.error("remote_host query_deleted_open_files request failed: %r", exc)
        return _error_result(
            tool_name="query_deleted_open_files",
            source="remote_host",
            message=f"请求 Host Agent 失败: {exc}",
            error_code="remote_host_request_error",
        )
    except json.JSONDecodeError as exc:
        logger.error("remote_host query_deleted_open_files invalid json: %r", exc)
        return _error_result(
            tool_name="query_deleted_open_files",
            source="remote_host",
            message="Host Agent 返回了非法 JSON",
            error_code="remote_host_invalid_json",
            status_code=status_code if "status_code" in locals() else None,
        )

    if not isinstance(payload, dict):
        logger.error("remote_host query_deleted_open_files invalid payload type: %s", type(payload).__name__)
        return _error_result(
            tool_name="query_deleted_open_files",
            source="remote_host",
            message="Host Agent 返回结构不符合预期",
            error_code="remote_host_invalid_payload",
            status_code=status_code,
        )

    if payload.get("ok") is False:
        logger.error("remote_host query_deleted_open_files returned structured error: %s", payload)
        return _error_result(
            tool_name="query_deleted_open_files",
            source="remote_host",
            message=str(payload.get("message") or "Host Agent deleted-open-files 接口返回错误"),
            error_code=str(payload.get("error_code") or "remote_host_deleted_open_files_error"),
            status_code=status_code,
        )

    normalized_files = []
    for item in _extract_deleted_open_file_items(payload):
        process_name = str(item.get("process") or item.get("process_name") or "")
        normalized_files.append(
            {
                "process": process_name,
                "pid": item.get("pid"),
                "file": item.get("file") or item.get("path"),
                "state": item.get("state") or "deleted",
                "size_gb": _to_float(item.get("size_gb")),
                "size_mb": _to_float(item.get("size_mb")),
                "suggestion": item.get("suggestion") or (
                    f"在业务低峰平滑重启 {process_name}，释放已删除但未归还的磁盘空间"
                    if process_name
                    else "确认句柄所属进程后再安排平滑重启释放空间"
                ),
                "source": "remote_host",
            }
        )

    return {
        "ok": True,
        "files": normalized_files,
        "total": int(payload.get("total") or len(normalized_files)),
        "total_raw": payload.get("total_raw"),
        "total_filtered": payload.get("total_filtered"),
        "filtered_out_count": payload.get("filtered_out_count"),
        "filters_applied": payload.get("filters_applied") or [],
        "message": payload.get("message") or "",
        "source": "remote_host",
    }


def query_docker_disk_usage_data() -> dict[str, Any]:
    provider = get_monitor_provider_name()
    _log_provider_context(provider, "query_docker_disk_usage")

    if provider == "mock":
        payload = _load_disk_mock_data()
        result = dict(payload.get("docker_usage", {}))
        result["source"] = "mock"
        return result

    try:
        status_code, payload = _request_remote_json("/api/v1/docker/disk-usage")
    except ValueError as exc:
        logger.error("remote_host query_docker_disk_usage config error: %s", exc)
        return _error_result(
            tool_name="query_docker_disk_usage",
            source="remote_host",
            message=str(exc),
            error_code="remote_host_config_error",
        )
    except httpx.TimeoutException as exc:
        logger.error("remote_host query_docker_disk_usage timeout: %r", exc)
        return _error_result(
            tool_name="query_docker_disk_usage",
            source="remote_host",
            message="请求 Host Agent 超时",
            error_code="remote_host_timeout",
        )
    except httpx.HTTPStatusError as exc:
        logger.error("remote_host query_docker_disk_usage http error: %s", exc)
        return _error_result(
            tool_name="query_docker_disk_usage",
            source="remote_host",
            message=f"Host Agent 返回非 200 状态码: {exc.response.status_code}",
            error_code="remote_host_http_error",
            status_code=exc.response.status_code,
        )
    except httpx.HTTPError as exc:
        logger.error("remote_host query_docker_disk_usage request failed: %r", exc)
        return _error_result(
            tool_name="query_docker_disk_usage",
            source="remote_host",
            message=f"请求 Host Agent 失败: {exc}",
            error_code="remote_host_request_error",
        )
    except json.JSONDecodeError as exc:
        logger.error("remote_host query_docker_disk_usage invalid json: %r", exc)
        return _error_result(
            tool_name="query_docker_disk_usage",
            source="remote_host",
            message="Host Agent 返回了非法 JSON",
            error_code="remote_host_invalid_json",
            status_code=status_code if "status_code" in locals() else None,
        )

    result = _extract_docker_payload(payload)
    if result.get("ok") is False:
        logger.error("remote_host query_docker_disk_usage returned structured error: %s", result)
        return _error_result(
            tool_name="query_docker_disk_usage",
            source="remote_host",
            message=str(result.get("message") or "Host Agent Docker 接口返回错误"),
            error_code=str(result.get("error_code") or "remote_host_docker_error"),
            status_code=status_code,
        )

    adapted = {
        "images_gb": _to_float(result.get("images_gb")),
        "containers_gb": _to_float(result.get("containers_gb")),
        "volumes_gb": _to_float(result.get("volumes_gb")),
        "build_cache_gb": _to_float(result.get("build_cache_gb")),
        "source": "remote_host",
    }
    parts = [
        part
        for part in (
            adapted["images_gb"],
            adapted["containers_gb"],
            adapted["volumes_gb"],
            adapted["build_cache_gb"],
        )
        if part is not None
    ]
    adapted["total_gb"] = _to_float(result.get("total_gb"))
    if adapted["total_gb"] is None and parts:
        adapted["total_gb"] = round(sum(parts), 1)
    return adapted
