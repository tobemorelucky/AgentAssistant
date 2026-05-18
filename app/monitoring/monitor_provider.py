"""Provider abstraction for AIOps monitor data sources."""

from __future__ import annotations

import json
import logging
import re
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
MONITOR_MOCK_PATH = ROOT_DIR / "mock_data" / "disk.json"
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


def _load_monitor_mock_data() -> dict[str, Any]:
    with MONITOR_MOCK_PATH.open("r", encoding="utf-8") as fh:
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
        raise ValueError("AIOPS_REMOTE_HOST_BASE_URL 未配置。")

    headers = {"Accept": "application/json"}
    token = get_remote_host_token()
    if token:
        headers["X-Host-Agent-Token"] = token

    url = f"{base_url}{path}"
    if httpx.Client is None:  # type: ignore[truthy-function]
        raise RuntimeError("当前环境缺少 httpx，无法访问 Host Agent。")
    with httpx.Client(
        timeout=DEFAULT_REMOTE_TIMEOUT,
        follow_redirects=True,
        trust_env=True,
    ) as client:
        response = client.get(url, params=params or {}, headers=headers)
        response.raise_for_status()
        return response.status_code, response.json()


def _remote_request_or_error(
    *,
    tool_name: str,
    path: str,
    params: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, int | None]:
    status_code: int | None = None
    try:
        status_code, payload = _request_remote_json(path, params)
    except ValueError as exc:
        logger.error("remote_host %s config error: %s", tool_name, exc)
        return (
            _error_result(
                tool_name=tool_name,
                source="remote_host",
                message=str(exc),
                error_code="remote_host_config_error",
                extra=extra,
            ),
            None,
        )
    except httpx.TimeoutException as exc:
        logger.error("remote_host %s timeout: %r", tool_name, exc)
        return (
            _error_result(
                tool_name=tool_name,
                source="remote_host",
                message="请求 Host Agent 超时。",
                error_code="remote_host_timeout",
                extra=extra,
            ),
            None,
        )
    except httpx.HTTPStatusError as exc:
        logger.error("remote_host %s http error: %r", tool_name, exc)
        return (
            _error_result(
                tool_name=tool_name,
                source="remote_host",
                message=f"Host Agent 返回非 200 状态码: {exc.response.status_code}",
                error_code="remote_host_http_error",
                status_code=exc.response.status_code,
                extra=extra,
            ),
            getattr(exc.response, "status_code", None),
        )
    except httpx.HTTPError as exc:
        logger.error("remote_host %s request failed: %r", tool_name, exc)
        return (
            _error_result(
                tool_name=tool_name,
                source="remote_host",
                message=f"访问 Host Agent 失败: {exc}",
                error_code="remote_host_request_error",
                extra=extra,
            ),
            None,
        )
    except json.JSONDecodeError as exc:
        logger.error("remote_host %s invalid json: %r", tool_name, exc)
        return (
            _error_result(
                tool_name=tool_name,
                source="remote_host",
                message="Host Agent 返回了非法 JSON。",
                error_code="remote_host_invalid_json",
                status_code=status_code,
                extra=extra,
            ),
            status_code,
        )

    if not isinstance(payload, dict):
        logger.error("remote_host %s invalid payload type: %s", tool_name, type(payload).__name__)
        return (
            _error_result(
                tool_name=tool_name,
                source="remote_host",
                message="Host Agent 返回结构不是对象。",
                error_code="remote_host_invalid_payload",
                status_code=status_code,
                extra=extra,
            ),
            status_code,
        )

    if payload.get("ok") is False:
        logger.error("remote_host %s returned structured error: %s", tool_name, payload)
        return (
            _error_result(
                tool_name=tool_name,
                source="remote_host",
                message=str(payload.get("message") or f"{tool_name} 执行失败"),
                error_code=str(payload.get("error_code") or f"{tool_name}_error"),
                status_code=status_code,
                extra=extra,
            ),
            status_code,
        )

    payload["source"] = "remote_host"
    return payload, status_code


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _status_from_usage(usage_percent: Any) -> str:
    usage = _to_float(usage_percent)
    if usage is None:
        return "unknown"
    if usage >= 90:
        return "critical"
    if usage >= 80:
        return "warning"
    return "healthy"


def _size_string_to_gb(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([A-Za-z]+)\s*$", text)
    if not match:
        return _to_float(value)
    number = _to_float(match.group(1))
    unit = match.group(2).upper()
    if number is None:
        return None
    if unit in {"GB", "GIB"}:
        return round(number, 2)
    if unit in {"MB", "MIB"}:
        return round(number / 1024, 2)
    if unit in {"KB", "KIB"}:
        return round(number / (1024**2), 4)
    if unit in {"B", "BYTE", "BYTES"}:
        return round(number / (1024**3), 4)
    return None


def _directory_reason(path: str) -> str:
    mapping = {
        "/var/log": "日志目录持续增长，通常需要结合 logrotate 与业务输出量一起排查。",
        "/var/lib/docker": "Docker 镜像、容器层或 build cache 占用较大。",
        "/tmp": "临时文件目录占用偏高，需确认是否存在遗留文件。",
        "/app/cache": "应用缓存目录可能存在过期缓存或缓存策略异常。",
    }
    return mapping.get(path, "该目录占用较大，建议结合目录用途进一步确认增长来源。")


def _extract_items(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        data = payload.get("data")
        if isinstance(data, dict):
            return _extract_items(data, *keys)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _extract_docker_payload(payload: dict[str, Any]) -> dict[str, Any]:
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
        return normalized
    return payload


def _normalize_memory_summary(payload: dict[str, Any]) -> dict[str, Any]:
    available_gb = _to_float(payload.get("available_gb"))
    if available_gb is None:
        available_gb = _to_float(payload.get("free_gb"))
    usage_percent = _to_float(payload.get("usage_percent"))
    return {
        "ok": True,
        "host": payload.get("host") or payload.get("hostname") or "unknown-host",
        "total_gb": _to_float(payload.get("total_gb")),
        "used_gb": _to_float(payload.get("used_gb")),
        "available_gb": available_gb,
        "usage_percent": usage_percent,
        "swap_total_gb": _to_float(payload.get("swap_total_gb")),
        "swap_used_gb": _to_float(payload.get("swap_used_gb")),
        "status": payload.get("status") or _status_from_usage(usage_percent),
        "source": payload.get("source") or "remote_host",
    }


def _normalize_cpu_summary(payload: dict[str, Any]) -> dict[str, Any]:
    usage_percent = _to_float(payload.get("usage_percent"))
    if usage_percent is None:
        usage_percent = _to_float(payload.get("cpu_percent"))
    cores = payload.get("cores")
    if cores is None:
        cores = payload.get("logical_cpu_count")
    load_1 = _to_float(payload.get("load_1"))
    if load_1 is None:
        load_1 = _to_float(payload.get("load_1m"))
    load_5 = _to_float(payload.get("load_5"))
    if load_5 is None:
        load_5 = _to_float(payload.get("load_5m"))
    load_15 = _to_float(payload.get("load_15"))
    if load_15 is None:
        load_15 = _to_float(payload.get("load_15m"))
    return {
        "ok": True,
        "host": payload.get("host") or payload.get("hostname") or "unknown-host",
        "usage_percent": usage_percent,
        "cores": cores,
        "logical_cpu_count": cores,
        "load_1": load_1,
        "load_5": load_5,
        "load_15": load_15,
        "status": payload.get("status") or _status_from_usage(usage_percent),
        "source": payload.get("source") or "remote_host",
    }


def _normalize_processes(
    payload: dict[str, Any],
    *,
    percent_field: str,
    rss_field: str | None = None,
) -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    for item in _extract_items(payload, "processes", "items", "results", "entries"):
        percent = _to_float(item.get(percent_field))
        normalized = {
            "pid": item.get("pid"),
            "process_name": item.get("process_name") or item.get("name") or item.get("command") or "unknown",
            "command": item.get("command") or "",
            "source": item.get("source") or payload.get("source") or "remote_host",
        }
        normalized[percent_field] = percent
        if rss_field:
            rss_mb = _to_float(item.get("rss_mb"))
            rss_gb = _to_float(item.get("rss_gb"))
            if rss_gb is None and rss_mb is not None:
                rss_gb = round(rss_mb / 1024, 2)
            if rss_mb is None and rss_gb is not None:
                rss_mb = round(rss_gb * 1024, 1)
            normalized["rss_mb"] = rss_mb
            normalized["rss_gb"] = rss_gb
        if "threads" in item:
            normalized["threads"] = item.get("threads")
        processes.append(normalized)
    return processes


def get_disk_usage_data(hostname: str | None = None, mount: str = "/") -> dict[str, Any]:
    provider = get_monitor_provider_name()
    _log_provider_context(provider, "get_disk_usage")

    if provider == "mock":
        payload = _load_monitor_mock_data()
        disk_usage = dict(payload.get("disk_usage", {}))
        if hostname:
            disk_usage["host"] = hostname
        disk_usage["mount"] = mount or disk_usage.get("mount", "/")
        disk_usage["status"] = disk_usage.get("status") or _status_from_usage(disk_usage.get("usage_percent"))
        disk_usage["source"] = "mock"
        return disk_usage

    payload, _ = _remote_request_or_error(
        tool_name="get_disk_usage",
        path="/api/v1/disk/usage",
        params={"mount": mount or "/"},
        extra={"host": hostname, "mount": mount or "/"},
    )
    if payload is None or payload.get("ok") is False:
        return payload or _error_result(tool_name="get_disk_usage", source="remote_host", message="unknown", error_code="unknown")

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
        payload = _load_monitor_mock_data()
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

    payload, status_code = _remote_request_or_error(
        tool_name="list_large_directories",
        path="/api/v1/disk/large-directories",
        params={"path": path or "/", "limit": limit},
        extra={"path": path or "/", "limit": limit},
    )
    if payload is None or payload.get("ok") is False:
        return payload or _error_result(
            tool_name="list_large_directories",
            source="remote_host",
            message="unknown",
            error_code="unknown",
            status_code=status_code,
        )

    directories = []
    for item in _extract_items(payload, "directories", "items", "results", "entries")[:limit]:
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
        payload = _load_monitor_mock_data()
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

    payload, status_code = _remote_request_or_error(
        tool_name="list_large_files",
        path="/api/v1/disk/large-files",
        params={"path": path or "/", "min_size_mb": min_size_mb, "limit": limit},
        extra={"path": path or "/", "min_size_mb": min_size_mb, "limit": limit},
    )
    if payload is None or payload.get("ok") is False:
        return payload or _error_result(
            tool_name="list_large_files",
            source="remote_host",
            message="unknown",
            error_code="unknown",
            status_code=status_code,
        )

    normalized_files = []
    for item in _extract_items(payload, "files", "items", "results", "entries")[:limit]:
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
        payload = _load_monitor_mock_data()
        files = []
        for item in list(payload.get("deleted_open_files", []) or []):
            file_item = dict(item)
            process_name = file_item.get("process") or file_item.get("process_name") or ""
            file_item["process"] = process_name
            file_item["file"] = file_item.get("file") or file_item.get("path")
            file_item["suggestion"] = file_item.get("suggestion") or (
                f"建议在评估业务影响后重启进程 {process_name} 释放已删除文件占用空间。"
                if process_name
                else "建议在评估业务影响后重启相关进程释放已删除文件占用空间。"
            )
            file_item["source"] = "mock"
            files.append(file_item)
        return {
            "ok": True,
            "files": files,
            "total": len(files),
            "source": "mock",
        }

    payload, status_code = _remote_request_or_error(
        tool_name="query_deleted_open_files",
        path="/api/v1/disk/deleted-open-files",
    )
    if payload is None or payload.get("ok") is False:
        return payload or _error_result(
            tool_name="query_deleted_open_files",
            source="remote_host",
            message="unknown",
            error_code="unknown",
            status_code=status_code,
        )

    normalized_files = []
    for item in _extract_items(payload, "files", "items", "results", "entries"):
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
                    f"建议在评估业务影响后重启进程 {process_name} 释放已删除文件占用空间。"
                    if process_name
                    else "建议在评估业务影响后重启相关进程释放已删除文件占用空间。"
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
        payload = _load_monitor_mock_data()
        result = dict(payload.get("docker_usage", {}))
        result["source"] = "mock"
        return result

    payload, status_code = _remote_request_or_error(
        tool_name="query_docker_disk_usage",
        path="/api/v1/docker/disk-usage",
    )
    if payload is None or payload.get("ok") is False:
        return payload or _error_result(
            tool_name="query_docker_disk_usage",
            source="remote_host",
            message="unknown",
            error_code="unknown",
            status_code=status_code,
        )

    result = _extract_docker_payload(payload)
    adapted = {
        "images_gb": _to_float(result.get("images_gb")),
        "containers_gb": _to_float(result.get("containers_gb")),
        "volumes_gb": _to_float(result.get("volumes_gb")),
        "build_cache_gb": _to_float(result.get("build_cache_gb")),
        "source": "remote_host",
    }
    parts = [
        value
        for value in (
            adapted["images_gb"],
            adapted["containers_gb"],
            adapted["volumes_gb"],
            adapted["build_cache_gb"],
        )
        if value is not None
    ]
    adapted["total_gb"] = _to_float(result.get("total_gb"))
    if adapted["total_gb"] is None and parts:
        adapted["total_gb"] = round(sum(parts), 1)
    return adapted


def get_memory_summary_data() -> dict[str, Any]:
    provider = get_monitor_provider_name()
    _log_provider_context(provider, "get_memory_summary")

    if provider == "mock":
        payload = _load_monitor_mock_data()
        result = _normalize_memory_summary(dict(payload.get("memory_summary", {})))
        result["source"] = "mock"
        return result

    payload, status_code = _remote_request_or_error(
        tool_name="get_memory_summary",
        path="/api/v1/system/memory-summary",
    )
    if payload is None or payload.get("ok") is False:
        return payload or _error_result(
            tool_name="get_memory_summary",
            source="remote_host",
            message="unknown",
            error_code="unknown",
            status_code=status_code,
        )
    return _normalize_memory_summary(payload)


def list_top_memory_processes_data(limit: int = 10) -> dict[str, Any]:
    provider = get_monitor_provider_name()
    _log_provider_context(provider, "list_top_memory_processes")

    if provider == "mock":
        payload = _load_monitor_mock_data()
        processes = list(payload.get("top_memory_processes", []) or [])[:limit]
        normalized = _normalize_processes({"processes": processes, "limit": limit, "source": "mock"}, percent_field="memory_percent", rss_field="rss")
        return {
            "ok": True,
            "processes": normalized,
            "limit": limit,
            "source": "mock",
        }

    payload, status_code = _remote_request_or_error(
        tool_name="list_top_memory_processes",
        path="/api/v1/process/top-memory",
        params={"limit": limit},
        extra={"limit": limit},
    )
    if payload is None or payload.get("ok") is False:
        return payload or _error_result(
            tool_name="list_top_memory_processes",
            source="remote_host",
            message="unknown",
            error_code="unknown",
            status_code=status_code,
        )

    return {
        "ok": True,
        "processes": _normalize_processes(payload, percent_field="memory_percent", rss_field="rss"),
        "limit": int(payload.get("limit") or limit),
        "source": "remote_host",
    }


def get_cpu_summary_data() -> dict[str, Any]:
    provider = get_monitor_provider_name()
    _log_provider_context(provider, "get_cpu_summary")

    if provider == "mock":
        payload = _load_monitor_mock_data()
        result = _normalize_cpu_summary(dict(payload.get("cpu_summary", {})))
        result["source"] = "mock"
        return result

    payload, status_code = _remote_request_or_error(
        tool_name="get_cpu_summary",
        path="/api/v1/system/cpu-summary",
    )
    if payload is None or payload.get("ok") is False:
        return payload or _error_result(
            tool_name="get_cpu_summary",
            source="remote_host",
            message="unknown",
            error_code="unknown",
            status_code=status_code,
        )
    return _normalize_cpu_summary(payload)


def list_top_cpu_processes_data(limit: int = 10) -> dict[str, Any]:
    provider = get_monitor_provider_name()
    _log_provider_context(provider, "list_top_cpu_processes")

    if provider == "mock":
        payload = _load_monitor_mock_data()
        processes = list(payload.get("top_cpu_processes", []) or [])[:limit]
        normalized = _normalize_processes({"processes": processes, "limit": limit, "source": "mock"}, percent_field="cpu_percent")
        return {
            "ok": True,
            "processes": normalized,
            "limit": limit,
            "source": "mock",
        }

    payload, status_code = _remote_request_or_error(
        tool_name="list_top_cpu_processes",
        path="/api/v1/process/top-cpu",
        params={"limit": limit},
        extra={"limit": limit},
    )
    if payload is None or payload.get("ok") is False:
        return payload or _error_result(
            tool_name="list_top_cpu_processes",
            source="remote_host",
            message="unknown",
            error_code="unknown",
            status_code=status_code,
        )

    return {
        "ok": True,
        "processes": _normalize_processes(payload, percent_field="cpu_percent"),
        "limit": int(payload.get("limit") or limit),
        "source": "remote_host",
    }
