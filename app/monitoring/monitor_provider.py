"""Provider abstraction for AIOps monitor data sources."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

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
    provider = os.getenv("AIOPS_MONITOR_PROVIDER", "mock").strip().lower()
    return provider if provider in {"mock", "remote_host"} else "mock"


def get_remote_host_base_url() -> str:
    return os.getenv("AIOPS_REMOTE_HOST_BASE_URL", "").strip().rstrip("/")


def get_remote_host_token() -> str:
    return os.getenv("AIOPS_REMOTE_HOST_TOKEN", "").strip()


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
        headers["Authorization"] = f"Bearer {token}"

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
        for key in ("directories", "items", "results"):
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
        return payload
    return {}


def get_disk_usage_data(hostname: str | None = None, mount: str = "/") -> dict[str, Any]:
    provider = get_monitor_provider_name()
    logger.info("get_disk_usage using provider=%s", provider)

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
    logger.info("list_large_directories using provider=%s", provider)

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


def query_docker_disk_usage_data() -> dict[str, Any]:
    provider = get_monitor_provider_name()
    logger.info("query_docker_disk_usage using provider=%s", provider)

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
