"""Deterministic disk-cleanup helpers for the governed AIOps workflow."""

from __future__ import annotations

import json
import re
from textwrap import dedent
from typing import Any


DISK_SKILL_NAME = "disk_cleanup"
DISK_INTENT_NAME = "disk_diagnosis"
DISK_KNOWLEDGE_QUERY = "磁盘使用率过高 清理 runbook"
DISK_TOOL_SEQUENCE = [
    "get_disk_usage",
    "list_large_directories",
    "list_large_files",
    "query_deleted_open_files",
    "query_docker_disk_usage",
    "get_disk_cleanup_candidates",
    "retrieve_knowledge",
]
DISK_TOOL_ARGS: dict[str, dict[str, Any]] = {
    "get_disk_usage": {"mount": "/"},
    "list_large_directories": {"path": "/", "limit": 10},
    "list_large_files": {"path": "/", "min_size_mb": 100, "limit": 20},
    "query_deleted_open_files": {},
    "query_docker_disk_usage": {},
    "get_disk_cleanup_candidates": {},
    "retrieve_knowledge": {"query": DISK_KNOWLEDGE_QUERY},
}

DISK_KEYWORDS = (
    "disk",
    "disk usage",
    "disk full",
    "high disk",
    "no space left",
    "storage",
    "磁盘",
    "硬盘",
    "磁盘满",
    "硬盘满",
    "清理空间",
    "清理缓存",
)


_PSEUDO_FS_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/var/run")
_PSEUDO_TMP_PREFIX = "/tmp/.mount"


def _normalize_posix_path(path: Any) -> str:
    text = str(path or "").strip().replace("\\", "/")
    if not text:
        return ""
    if not text.startswith("/"):
        text = f"/{text.lstrip('/')}"
    return re.sub(r"/{2,}", "/", text.rstrip("/")) or "/"


def is_pseudo_filesystem_path(path: Any) -> bool:
    normalized = _normalize_posix_path(path)
    if not normalized:
        return False
    for prefix in _PSEUDO_FS_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return True
    return normalized.startswith(_PSEUDO_TMP_PREFIX)


def filter_disk_directory_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if not is_pseudo_filesystem_path(item.get("path"))]


def filter_disk_file_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if not is_pseudo_filesystem_path(item.get("path"))]


def _extract_embedded_json(text: str) -> Any:
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        candidate = text[brace_start : brace_end + 1].replace('\\"', '"')
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
    return None


def is_disk_cleanup_skill(skill: dict[str, Any] | str) -> bool:
    if isinstance(skill, dict):
        return str(skill.get("name") or skill.get("skill_name") or "") == DISK_SKILL_NAME
    return str(skill) == DISK_SKILL_NAME


def is_disk_cleanup_request(input_text: str, matched_skills: list[dict[str, Any]] | None = None) -> bool:
    normalized = (input_text or "").lower()
    if any(is_disk_cleanup_skill(skill) for skill in matched_skills or []):
        return True
    return any(keyword in normalized for keyword in DISK_KEYWORDS)


def build_disk_cleanup_plan() -> list[str]:
    return [
        "调用 get_disk_usage 获取当前主机根挂载点 / 的磁盘使用率证据。",
        "调用 list_large_directories 获取 / 下的高占用目录排行，定位 Top 目录占用。",
        "调用 list_large_files 获取 / 下的大文件清单，定位最占空间的文件。",
        "调用 query_deleted_open_files 检查是否存在已删除但仍被进程持有的文件句柄。",
        "调用 query_docker_disk_usage 采集 Docker 镜像、容器、卷和构建缓存占用。",
        "调用 get_disk_cleanup_candidates 汇总可安全清理项、需人工确认项和禁止自动清理项。",
        f"调用 retrieve_knowledge 检索“{DISK_KNOWLEDGE_QUERY}”相关 runbook，补充清理原则与风险提示。",
    ]


def extract_disk_tool_name(step: str) -> str | None:
    for tool_name in DISK_TOOL_SEQUENCE:
        if tool_name in (step or ""):
            return tool_name
    return None


def extract_disk_tools_from_steps(steps: list[Any]) -> list[str]:
    tools: list[str] = []
    for step in steps or []:
        tool_name = extract_disk_tool_name(str(step))
        if tool_name:
            tools.append(tool_name)
    return tools


def unwrap_structured_payload(value: Any) -> Any:
    """Unwrap MCP/LangChain text blocks and JSON strings into Python objects."""
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        try:
            return unwrap_structured_payload(json.loads(text))
        except json.JSONDecodeError:
            embedded = _extract_embedded_json(text)
            if embedded is not None:
                return unwrap_structured_payload(embedded)
            return text

    if isinstance(value, list):
        if value and all(isinstance(item, dict) and "text" in item for item in value):
            combined = "\n".join(str(item.get("text", "")) for item in value if item.get("text"))
            return unwrap_structured_payload(combined)
        return [unwrap_structured_payload(item) for item in value]

    if isinstance(value, dict):
        if value.get("type") == "text" and "text" in value:
            return unwrap_structured_payload(value.get("text", ""))
        if "structuredContent" in value:
            return unwrap_structured_payload(value["structuredContent"])
        if "content" in value and len(value) <= 2:
            return unwrap_structured_payload(value["content"])
        return {
            key: unwrap_structured_payload(item)
            for key, item in value.items()
            if key not in {"id", "type"}
        }

    return value


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_tool_error(payload: Any) -> bool:
    return isinstance(payload, dict) and payload.get("ok") is False


def _error_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": False,
        "source": payload.get("source") or "unknown",
        "message": payload.get("message") or "工具返回错误",
        "error_code": payload.get("error_code") or "tool_error",
    }


def _disk_status(usage_percent: float | None) -> str:
    if usage_percent is None:
        return "unknown"
    if usage_percent >= 90:
        return "critical"
    if usage_percent >= 80:
        return "warning"
    return "healthy"


def _directory_reason(path: str) -> str:
    mapping = {
        "/var/log": "业务日志与归档日志堆积。",
        "/var/lib/docker": "Docker 镜像、卷或构建缓存占用偏高。",
        "/tmp": "临时文件未定期清理。",
        "/app/cache": "应用缓存目录未回收。",
    }
    return mapping.get(path, "该目录占用较高，需要结合业务场景进一步确认。")


def _file_safe_action(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".log"):
        return "先确认用途，再执行轮转、压缩或归档。"
    if "cache" in lowered or "swap" in lowered:
        return "需要先确认是否可回收，再安排处理。"
    return "需要结合业务影响评估后再处理。"


def _file_risk(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".log"):
        return "直接删除可能影响审计、排障或业务写入。"
    if "docker" in lowered:
        return "可能影响镜像构建链路或在线容器。"
    if "swap" in lowered:
        return "swap 文件涉及系统内存交换，不应直接删除。"
    return "需要确认文件用途后再处理。"


def _deleted_open_suggestion(process_name: str) -> str:
    if process_name:
        return f"在业务低峰平滑重启 {process_name}，释放已删除但未归还的磁盘空间。"
    return "确认句柄所属进程后，再安排平滑重启释放空间。"


def _primary_evidence_source(evidence: dict[str, Any]) -> str:
    for tool_name in (
        "get_disk_usage",
        "list_large_directories",
        "list_large_files",
        "query_deleted_open_files",
        "query_docker_disk_usage",
    ):
        payload = evidence.get(tool_name, {})
        if isinstance(payload, dict) and payload.get("source") in {"remote_host", "mock"}:
            return str(payload.get("source"))
    return "mock"


def _is_remote_realtime_mode(evidence: dict[str, Any]) -> bool:
    return _primary_evidence_source(evidence) == "remote_host"


def _source_statement(source: str) -> str:
    if source == "remote_host":
        return "本次结论基于远程 Host Agent 实时采集数据，并结合本地 runbook 进行分析。"
    return "本次结论基于 mock 现场数据，并结合本地 runbook 进行分析。"


def normalize_disk_tool_result(tool_name: str, raw_result: Any) -> Any:
    """Normalize disk tool payloads into stable JSON-serializable structures."""
    payload = unwrap_structured_payload(raw_result)

    if tool_name == "get_disk_usage":
        data = payload if isinstance(payload, dict) else {}
        if _is_tool_error(data):
            return {
                **_error_metadata(data),
                "host": data.get("host"),
                "mount": data.get("mount") or "/",
                "usage_percent": None,
                "used_gb": None,
                "total_gb": None,
                "available_gb": None,
                "status": "unknown",
            }
        usage_percent = _to_float(data.get("usage_percent"))
        return {
            "ok": True,
            "host": data.get("host") or "该字段未返回",
            "mount": data.get("mount") or "/",
            "usage_percent": usage_percent,
            "used_gb": _to_float(data.get("used_gb")),
            "total_gb": _to_float(data.get("total_gb")),
            "available_gb": _to_float(data.get("available_gb")),
            "status": data.get("status") or _disk_status(usage_percent),
            "source": data.get("source") or "mock",
        }

    if tool_name == "list_large_directories":
        data = payload if isinstance(payload, dict) else {}
        if _is_tool_error(data):
            return {
                **_error_metadata(data),
                "path": data.get("path") or "/",
                "limit": int(data.get("limit") or 10),
                "directories": [],
            }
        directories = data.get("directories") if isinstance(data.get("directories"), list) else []
        normalized = []
        for item in directories:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            normalized.append(
                {
                    "path": path,
                    "size_gb": _to_float(item.get("size_gb")),
                    "reason": item.get("reason") or _directory_reason(path),
                    "source": item.get("source") or data.get("source") or "mock",
                }
            )
        normalized = filter_disk_directory_items(normalized)
        return {
            "ok": True,
            "path": data.get("path") or "/",
            "limit": int(data.get("limit") or len(normalized) or 10),
            "directories": normalized,
            "source": data.get("source") or "mock",
        }

    if tool_name == "list_large_files":
        data = payload if isinstance(payload, dict) else {}
        if _is_tool_error(data):
            return {
                **_error_metadata(data),
                "path": data.get("path") or "/",
                "scan_root": data.get("scan_root") or data.get("path") or "/",
                "min_size_mb": int(data.get("min_size_mb") or 100),
                "limit": int(data.get("limit") or 20),
                "files": [],
                "warnings": data.get("warnings") or [],
                "scan_incomplete": bool(data.get("scan_incomplete")),
                "skipped_paths": data.get("skipped_paths") or [],
                "skipped_count": int(data.get("skipped_count") or 0),
                "permission_denied_count": int(data.get("permission_denied_count") or 0),
            }
        files = data.get("files") if isinstance(data.get("files"), list) else []
        normalized = []
        for item in files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            size_gb = _to_float(item.get("size_gb"))
            size_mb = _to_float(item.get("size_mb"))
            if size_gb is None and size_mb is not None:
                size_gb = round(size_mb / 1024, 2)
            if size_mb is None and size_gb is not None:
                size_mb = round(size_gb * 1024, 1)
            normalized.append(
                {
                    "path": path,
                    "size_gb": size_gb,
                    "size_mb": size_mb,
                    "scan_root": item.get("scan_root") or data.get("scan_root") or data.get("path") or "/",
                    "warning": item.get("warning") or "",
                    "safe_action": item.get("safe_action") or _file_safe_action(path),
                    "risk": item.get("risk") or _file_risk(path),
                    "source": item.get("source") or data.get("source") or "mock",
                }
            )
        normalized = filter_disk_file_items(normalized)
        return {
            "ok": True,
            "source": data.get("source") or "mock",
            "path": data.get("path") or "/",
            "scan_root": data.get("scan_root") or data.get("path") or "/",
            "min_size_mb": int(data.get("min_size_mb") or 100),
            "limit": int(data.get("limit") or len(normalized) or 20),
            "files": normalized,
            "warnings": data.get("warnings") or [],
            "scan_incomplete": bool(data.get("scan_incomplete")),
            "skipped_paths": data.get("skipped_paths") or [],
            "skipped_count": int(data.get("skipped_count") or 0),
            "permission_denied_count": int(data.get("permission_denied_count") or 0),
        }

    if tool_name == "query_deleted_open_files":
        data = payload if isinstance(payload, dict) else {}
        if _is_tool_error(data):
            return {
                **_error_metadata(data),
                "files": [],
                "total": 0,
                "total_raw": data.get("total_raw"),
                "total_filtered": data.get("total_filtered"),
                "filtered_out_count": data.get("filtered_out_count"),
                "filters_applied": data.get("filters_applied") or [],
            }
        files = data.get("files") if isinstance(data.get("files"), list) else []
        normalized = []
        for item in files:
            if not isinstance(item, dict):
                continue
            process_name = str(item.get("process") or item.get("process_name") or "")
            file_path = str(item.get("file") or item.get("path") or "")
            size_gb = _to_float(item.get("size_gb"))
            size_mb = _to_float(item.get("size_mb"))
            if size_gb is None and size_mb is not None:
                size_gb = round(size_mb / 1024, 2)
            normalized.append(
                {
                    "process": process_name,
                    "pid": item.get("pid"),
                    "file": file_path,
                    "state": item.get("state") or "deleted",
                    "size_gb": size_gb,
                    "size_mb": size_mb,
                    "suggestion": item.get("suggestion") or _deleted_open_suggestion(process_name),
                    "source": item.get("source") or data.get("source") or "mock",
                }
            )
        return {
            "ok": True,
            "source": data.get("source") or "mock",
            "files": normalized,
            "total": int(data.get("total") or len(normalized)),
            "total_raw": data.get("total_raw"),
            "total_filtered": data.get("total_filtered"),
            "filtered_out_count": data.get("filtered_out_count"),
            "filters_applied": data.get("filters_applied") or [],
            "message": data.get("message") or "",
        }

    if tool_name == "query_docker_disk_usage":
        data = payload if isinstance(payload, dict) else {}
        if _is_tool_error(data):
            return {
                **_error_metadata(data),
                "images_gb": None,
                "containers_gb": None,
                "volumes_gb": None,
                "build_cache_gb": None,
                "total_gb": None,
            }
        images_gb = _to_float(data.get("images_gb"))
        containers_gb = _to_float(data.get("containers_gb"))
        volumes_gb = _to_float(data.get("volumes_gb"))
        build_cache_gb = _to_float(data.get("build_cache_gb"))
        total_gb = _to_float(data.get("total_gb"))
        if total_gb is None:
            parts = [part for part in [images_gb, containers_gb, volumes_gb, build_cache_gb] if part is not None]
            total_gb = round(sum(parts), 1) if parts else None
        return {
            "ok": True,
            "images_gb": images_gb,
            "containers_gb": containers_gb,
            "volumes_gb": volumes_gb,
            "build_cache_gb": build_cache_gb,
            "total_gb": total_gb,
            "source": data.get("source") or "mock",
        }

    if tool_name == "get_disk_cleanup_candidates":
        data = payload if isinstance(payload, dict) else {}

        def normalize_items(items: Any, *, needs_reason: bool = False) -> list[dict[str, Any]]:
            normalized_items: list[dict[str, Any]] = []
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict):
                    continue
                normalized_item = {"item": item.get("item") or ""}
                if "size_gb" in item:
                    normalized_item["size_gb"] = _to_float(item.get("size_gb"))
                if needs_reason:
                    normalized_item["reason"] = item.get("reason") or "高风险或禁止自动执行。"
                else:
                    normalized_item["suggestion"] = item.get("suggestion") or "需要人工确认后再执行。"
                normalized_items.append(normalized_item)
            return normalized_items

        return {
            "source": data.get("source") or "mock",
            "safe": normalize_items(data.get("safe")),
            "need_approval": normalize_items(data.get("need_approval")),
            "forbidden": normalize_items(data.get("forbidden"), needs_reason=True),
        }

    return payload


def _format_number(value: Any, suffix: str = "") -> str:
    number = _to_float(value)
    if number is None:
        return "该字段未返回"
    if number.is_integer():
        return f"{int(number)}{suffix}"
    return f"{number:.1f}{suffix}"


def _format_percent(value: Any) -> str:
    return _format_number(value, "%")


def _join_or_missing(lines: list[str], empty_text: str) -> str:
    return "\n".join(lines) if lines else empty_text


def _short_knowledge_summary(value: Any) -> str:
    payload = unwrap_structured_payload(value)
    if isinstance(payload, str):
        return payload[:280] if payload else "未返回本地 runbook 内容。"
    return json.dumps(payload, ensure_ascii=False)[:280]


def summarize_disk_tool_result(tool_name: str, raw_result: Any) -> str:
    """Create a human-readable summary for disk tool results."""
    result = normalize_disk_tool_result(tool_name, raw_result)

    if isinstance(result, dict) and result.get("ok") is False:
        return (
            f"source={result.get('source')}, "
            f"error_code={result.get('error_code')}, "
            f"message={result.get('message')}"
        )

    if tool_name == "get_disk_usage":
        return (
            f"source={result.get('source')}, host={result.get('host')}, mount={result.get('mount')}, "
            f"usage={_format_percent(result.get('usage_percent'))}, "
            f"used={_format_number(result.get('used_gb'), 'GB')}, "
            f"total={_format_number(result.get('total_gb'), 'GB')}, "
            f"available={_format_number(result.get('available_gb'), 'GB')}"
        )

    if tool_name == "list_large_directories":
        directories = result.get("directories", [])
        lines = [
            f"{item.get('path')}: {_format_number(item.get('size_gb'), 'GB')} ({item.get('reason')})"
            for item in directories[:5]
        ]
        return _join_or_missing(lines, "未返回目录占用结果。")

    if tool_name == "list_large_files":
        if result.get("ok") is False:
            return (
                f"source={result.get('source')}, error_code={result.get('error_code')}, "
                f"message={result.get('message')}"
            )
        files = result.get("files", [])
        lines = [
            f"{item.get('path')}: {_format_number(item.get('size_gb'), 'GB')}"
            + (f" | warning={item.get('warning')}" if item.get("warning") else "")
            for item in files[:5]
        ]
        summary = _join_or_missing(lines, "未返回大文件结果。")
        if result.get("scan_incomplete"):
            summary += " | scan_incomplete=True"
        if result.get("permission_denied_count"):
            summary += f" | permission_denied={result.get('permission_denied_count')}"
        return summary

    if tool_name == "query_deleted_open_files":
        if result.get("ok") is False:
            return (
                f"source={result.get('source')}, error_code={result.get('error_code')}, "
                f"message={result.get('message')}"
            )
        files = result.get("files", [])
        lines = [
            f"{item.get('process')} pid={item.get('pid')} file={item.get('file')} size={_format_number(item.get('size_gb'), 'GB')}"
            for item in files[:5]
        ]
        summary = _join_or_missing(lines, "未发现高价值 deleted open files。")
        if result.get("filtered_out_count"):
            summary += f" | filtered_out={result.get('filtered_out_count')}"
        return summary

    if tool_name == "query_docker_disk_usage":
        return (
            f"source={result.get('source')}, "
            f"images={_format_number(result.get('images_gb'), 'GB')}, "
            f"containers={_format_number(result.get('containers_gb'), 'GB')}, "
            f"volumes={_format_number(result.get('volumes_gb'), 'GB')}, "
            f"build_cache={_format_number(result.get('build_cache_gb'), 'GB')}, "
            f"total={_format_number(result.get('total_gb'), 'GB')}"
        )

    if tool_name == "get_disk_cleanup_candidates":
        safe = ", ".join(item.get("item", "") for item in result.get("safe", [])[:3]) or "无"
        approval = ", ".join(item.get("item", "") for item in result.get("need_approval", [])[:3]) or "无"
        forbidden = ", ".join(item.get("item", "") for item in result.get("forbidden", [])[:3]) or "无"
        return f"safe={safe} | need_approval={approval} | forbidden={forbidden}"

    if tool_name == "retrieve_knowledge":
        return _short_knowledge_summary(result)

    return json.dumps(result, ensure_ascii=False)[:280] if not isinstance(result, str) else result[:280]


def parse_disk_step_results(past_steps: list[tuple[str, str]]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for step, result in past_steps:
        tool_name = extract_disk_tool_name(step)
        if not tool_name:
            continue
        evidence[tool_name] = normalize_disk_tool_result(tool_name, result)
    return evidence


def _evidence_list_lines(items: list[dict[str, Any]], formatter: Any, empty_text: str) -> str:
    lines = [formatter(item) for item in items if isinstance(item, dict)]
    return _join_or_missing(lines, empty_text)


def build_disk_cleanup_report(input_text: str, past_steps: list[tuple[str, str]]) -> str:
    evidence = parse_disk_step_results(past_steps)
    source = _primary_evidence_source(evidence)
    remote_realtime_mode = _is_remote_realtime_mode(evidence)

    disk_usage = evidence.get("get_disk_usage", {})
    directories_result = evidence.get("list_large_directories", {})
    directories = filter_disk_directory_items(list(directories_result.get("directories", [])))
    files_result = evidence.get("list_large_files", {})
    deleted_result = evidence.get("query_deleted_open_files", {})
    docker_usage = evidence.get("query_docker_disk_usage", {})
    cleanup_candidates = {} if remote_realtime_mode else evidence.get("get_disk_cleanup_candidates", {})
    knowledge = evidence.get("retrieve_knowledge", "")

    files = filter_disk_file_items(list(files_result.get("files", [])))
    deleted_open_files = list(deleted_result.get("files", []))

    host = disk_usage.get("host") or "该字段未返回"
    mount = disk_usage.get("mount") or "/"
    usage_percent = _format_percent(disk_usage.get("usage_percent"))
    used_gb = _format_number(disk_usage.get("used_gb"), "GB")
    total_gb = _format_number(disk_usage.get("total_gb"), "GB")
    available_gb = _format_number(disk_usage.get("available_gb"), "GB")

    if directories_result.get("ok") is False:
        top_directories = f"- 工具调用失败：{directories_result.get('message') or '该字段未返回'}"
    else:
        top_directories = _evidence_list_lines(
            directories[:5],
            lambda item: f"- `{item.get('path')}`: {_format_number(item.get('size_gb'), 'GB')}，原因：{item.get('reason') or '该字段未返回'}",
            "- 未返回目录占用结果。",
        )

    if files_result.get("ok") is False:
        top_files = f"- 工具调用失败：{files_result.get('message') or '该字段未返回'}"
    else:
        file_lines = []
        for item in files[:5]:
            line = f"- `{item.get('path')}`: {_format_number(item.get('size_gb'), 'GB')}"
            if item.get("warning"):
                line += f"；提示：{item.get('warning')}"
            file_lines.append(line)
        if files_result.get("scan_incomplete"):
            file_lines.append("- 本次扫描存在权限跳过，结果可能不完整。")
        if files_result.get("permission_denied_count"):
            file_lines.append(
                f"- 权限拒绝目录/文件计数：{files_result.get('permission_denied_count')}。"
            )
        if files_result.get("skipped_count"):
            file_lines.append(f"- 跳过路径计数：{files_result.get('skipped_count')}。")
        if files_result.get("warnings"):
            for warning in list(files_result.get("warnings", []))[:3]:
                file_lines.append(f"- 扫描提示：{warning}")
        top_files = _join_or_missing(
            file_lines,
            "- 未返回大文件结果。" if not remote_realtime_mode else "- 远程 Host Agent 未返回高占用大文件结果。",
        )

    if deleted_result.get("ok") is False:
        deleted_files_text = f"- 工具调用失败：{deleted_result.get('message') or '该字段未返回'}"
    else:
        deleted_lines = []
        for item in deleted_open_files[:5]:
            deleted_lines.append(
                f"- 进程 `{item.get('process') or '该字段未返回'}` pid={item.get('pid') or '该字段未返回'}，"
                f"文件 `{item.get('file') or '该字段未返回'}`，大小 {_format_number(item.get('size_gb'), 'GB')}，"
                f"建议：{item.get('suggestion') or '该字段未返回'}"
            )
        if not deleted_open_files:
            deleted_lines.append("在当前过滤策略下，未发现高价值 deleted open files 证据。")
        filtered_out_count = deleted_result.get("filtered_out_count")
        if filtered_out_count:
            deleted_lines.append(f"已过滤掉 {filtered_out_count} 条 memfd/极小噪声记录。")
        filters_applied = deleted_result.get("filters_applied") or []
        if filters_applied:
            deleted_lines.append(f"过滤策略：{', '.join(str(item) for item in filters_applied[:5])}")
        deleted_files_text = "\n".join(deleted_lines)

    if docker_usage.get("ok") is False:
        docker_text = f"- 工具调用失败：{docker_usage.get('message') or '该字段未返回'}"
    else:
        docker_text = "\n".join(
            [
                f"- Docker 总占用：{_format_number(docker_usage.get('total_gb'), 'GB')}",
                f"- images：{_format_number(docker_usage.get('images_gb'), 'GB')}",
                f"- containers：{_format_number(docker_usage.get('containers_gb'), 'GB')}",
                f"- volumes：{_format_number(docker_usage.get('volumes_gb'), 'GB')}",
                f"- build cache：{_format_number(docker_usage.get('build_cache_gb'), 'GB')}",
            ]
        )

    safe_text = _evidence_list_lines(
        list(cleanup_candidates.get("safe", [])),
        lambda item: f"- {item.get('item')}: {_format_number(item.get('size_gb'), 'GB')}，建议：{item.get('suggestion') or '该字段未返回'}",
        "- 当前 remote_host 模式下暂未接入实时清理候选项工具。" if remote_realtime_mode else "- 未返回可安全清理项。",
    )
    approval_text = _evidence_list_lines(
        list(cleanup_candidates.get("need_approval", [])),
        lambda item: f"- {item.get('item')}: {_format_number(item.get('size_gb'), 'GB')}，执行前需人工确认，建议：{item.get('suggestion') or '该字段未返回'}",
        "- 当前 remote_host 模式下暂未接入实时清理候选项工具。" if remote_realtime_mode else "- 未返回需人工确认项。",
    )
    forbidden_text = _evidence_list_lines(
        list(cleanup_candidates.get("forbidden", [])),
        lambda item: f"- {item.get('item')}：{item.get('reason') or '该字段未返回'}",
        "- 当前 remote_host 模式下暂未接入实时清理候选项工具。" if remote_realtime_mode else "- 未返回禁止自动清理项。",
    )

    root_cause_lines: list[str] = []
    if disk_usage.get("usage_percent") is not None:
        root_cause_lines.append(
            f"- 主挂载点 `{mount}` 当前使用率为 **{usage_percent}**，可用空间仅 **{available_gb}**。"
        )
    else:
        root_cause_lines.append("- 未返回磁盘使用率，当前无法量化总体磁盘压力。")

    if directories_result.get("ok") is False:
        root_cause_lines.append(f"- 目录占用工具调用失败：{directories_result.get('message') or '该字段未返回'}。")
    elif directories:
        leading_dirs = "、".join(
            f"`{item.get('path')}`（{_format_number(item.get('size_gb'), 'GB')}）"
            for item in directories[:3]
        )
        root_cause_lines.append(f"- 当前主要空间压力来自高占用目录：{leading_dirs}。")
    else:
        root_cause_lines.append("- 未返回高占用目录结果，无法进一步定位目录级压力来源。")

    if files_result.get("ok") is False:
        root_cause_lines.append(f"- 大文件扫描失败：{files_result.get('message') or '该字段未返回'}。")
    elif files:
        leading_files = "、".join(
            f"`{item.get('path')}`（{_format_number(item.get('size_gb'), 'GB')}）"
            for item in files[:3]
        )
        root_cause_lines.append(f"- 大文件证据显示重点关注对象为：{leading_files}。")
    else:
        root_cause_lines.append("- 未发现高占用大文件，或当前扫描结果中没有超过阈值的高价值文件。")

    if deleted_result.get("ok") is False:
        root_cause_lines.append(f"- deleted open files 检查失败：{deleted_result.get('message') or '该字段未返回'}。")
    elif deleted_open_files:
        first_deleted = deleted_open_files[0]
        root_cause_lines.append(
            f"- 检测到 deleted open file：`{first_deleted.get('file')}` 仍由 `{first_deleted.get('process')}` 持有，预计占用 {_format_number(first_deleted.get('size_gb'), 'GB')}。"
        )
    else:
        root_cause_lines.append("- 在当前过滤策略下，未发现高价值 deleted open files 证据。")

    if docker_usage.get("ok") is False:
        root_cause_lines.append(f"- Docker 占用工具调用失败：{docker_usage.get('message') or '该字段未返回'}。")
    elif docker_usage:
        root_cause_lines.append(
            f"- Docker 总占用约 {_format_number(docker_usage.get('total_gb'), 'GB')}，其中 build cache 约 {_format_number(docker_usage.get('build_cache_gb'), 'GB')}。"
        )
    else:
        root_cause_lines.append("- 未返回 Docker 占用结果。")

    key_evidence_lines: list[str] = []
    key_evidence_lines.append(f"- 磁盘使用率：**{usage_percent}**")

    if directories_result.get("ok") is False:
        key_evidence_lines.append(f"- 目录占用工具失败：{directories_result.get('error_code') or 'tool_error'}")
    elif directories:
        for item in directories[:2]:
            key_evidence_lines.append(f"- 目录 `{item.get('path')}`：**{_format_number(item.get('size_gb'), 'GB')}**")
    else:
        key_evidence_lines.append("- Top 目录：该字段未返回")

    if files_result.get("ok") is False:
        key_evidence_lines.append(f"- 大文件扫描失败：{files_result.get('error_code') or 'tool_error'}")
    elif files:
        for item in files[:2]:
            key_evidence_lines.append(f"- 文件 `{item.get('path')}`：**{_format_number(item.get('size_gb'), 'GB')}**")
    else:
        key_evidence_lines.append("- 高价值大文件：当前扫描结果未发现。")

    if deleted_result.get("ok") is False:
        key_evidence_lines.append(f"- deleted open files 检查失败：{deleted_result.get('error_code') or 'tool_error'}")
    elif deleted_result.get("filtered_out_count"):
        key_evidence_lines.append(
            f"- deleted open files 过滤噪声条数：**{deleted_result.get('filtered_out_count')}**"
        )

    if docker_usage.get("ok") is False:
        key_evidence_lines.append(f"- Docker 占用工具失败：{docker_usage.get('error_code') or 'tool_error'}")
    else:
        key_evidence_lines.append(f"- Docker build cache：**{_format_number(docker_usage.get('build_cache_gb'), 'GB')}**")

    report = dedent(
        f"""
        # AIOps 磁盘诊断报告

        ## 任务
        - {input_text}

        ## 磁盘使用率证据
        - 主机 `{host}`，挂载点 `{mount}`，当前使用率 **{usage_percent}**。
        - 已使用 **{used_gb} / {total_gb}**，可用空间 **{available_gb}**。
        - 当前状态：`{disk_usage.get('status') or '该字段未返回'}`。

        ## Top 目录占用
        {top_directories}

        ## Top 大文件
        {top_files}

        ## Docker 占用情况
        {docker_text}

        ## Deleted Open Files
        {deleted_files_text}

        ## 根因分析
        {chr(10).join(root_cause_lines)}

        ## 关键证据
        {chr(10).join(key_evidence_lines)}

        ## 影响范围
        - 诊断对象主机：`{host}`
        - 如果磁盘继续增长，可能影响日志写入、镜像构建、容器运行和业务临时文件落盘。
        - {_source_statement(source)}

        ## 可安全清理项
        {safe_text}

        ## 需人工确认项
        {approval_text}

        ## 禁止自动清理项
        {forbidden_text}

        ## 风险提示
        - 本次诊断 **没有执行任何删除操作**，仅输出证据和建议。
        - `rm -rf`、`docker system prune --volumes`、删除 `uploads/`、删除 Milvus/MinIO/etcd volumes 都必须视为高风险操作。
        - 若大文件扫描提示权限跳过，应先补充具备足够权限的离线扫描，再决定是否清理。

        ## 后续预防措施
        - 为高占用目录建立定期清理与容量阈值告警。
        - 持续监控 `/var/lib/docker` 的 images、volumes 和 build cache 增长趋势。
        - 对接磁盘 80% / 90% 分级告警并绑定 `HighDiskUsage` / `DiskFull` runbook。
        - 为 deleted open files 建立平滑重启和句柄释放 SOP。

        ## Runbook 参考
        - aiops-docs 提供的是本地 runbook 资料，不是实时日志。
        - 当前查询：`{DISK_KNOWLEDGE_QUERY}`
        - 参考摘要：{_short_knowledge_summary(knowledge)}
        """
    ).strip()
    return report


def build_disk_verifier_findings(report: str, past_steps: list[tuple[str, str]]) -> tuple[list[str], list[str], list[str], list[str]]:
    """Hard verification rules for disk-cleanup reports."""
    evidence = parse_disk_step_results(past_steps)
    source = _primary_evidence_source(evidence)
    files_result = evidence.get("list_large_files", {})
    deleted_result = evidence.get("query_deleted_open_files", {})
    directories_result = evidence.get("list_large_directories", {})
    directories = filter_disk_directory_items(list(directories_result.get("directories", [])))
    files = filter_disk_file_items(list(files_result.get("files", [])))
    docker = evidence.get("query_docker_disk_usage", {})
    cleanup = {} if _is_remote_realtime_mode(evidence) else evidence.get("get_disk_cleanup_candidates", {})
    disk_usage = evidence.get("get_disk_usage", {})

    findings: list[str] = []
    suggested: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []
    lowered = report.lower()

    if "unknown" in lowered:
        findings.append("报告中仍包含 unknown 占位词，说明字段映射不完整。")
        suggested.append("用“该字段未返回”替代 unknown，并补充真实工具字段。")

    if source == "remote_host":
        for forbidden_fragment in ("demo-server-01", "92.4%", "200GB"):
            if forbidden_fragment in report:
                findings.append("remote_host 模式的报告中仍残留 demo/mock 数值。")
                suggested.append("移除 demo-server-01 / 92.4% / 200GB 等 mock 残留，只保留远程实时证据。")
                break

    if disk_usage.get("usage_percent") is None or not re.search(r"\b\d+(?:\.\d+)?%", report):
        findings.append("报告没有给出具体磁盘使用率百分比。")
        missing.append("磁盘使用率")
        suggested.append("补充 get_disk_usage 返回的 usage_percent。")

    if directories_result.get("ok") is False:
        if "目录占用工具调用失败" not in report and "工具调用失败" not in report:
            findings.append("目录占用工具失败时，报告没有明确说明失败原因。")
            suggested.append("在 Top 目录占用段落中保留目录工具失败信息。")
    elif directories and len(directories) >= 2:
        mentioned_dirs = sum(1 for item in directories[:2] if item.get("path") and str(item["path"]) in report)
        if mentioned_dirs < 2:
            findings.append("报告没有覆盖至少 2 个 Top 目录证据。")
            missing.append("Top 目录")
            suggested.append("把 list_large_directories 的前 2 个目录写入报告。")
    elif "未返回目录占用结果" not in report and "无法进一步定位目录级压力来源" not in report:
        findings.append("目录证据不足，至少需要 2 个 Top 目录。")
        missing.append("Top 目录")
        suggested.append("补充 list_large_directories 结果后再生成报告。")

    if files_result.get("ok") is False:
        findings.append("大文件工具调用失败，报告应明确说明失败原因。")
        missing.append("Top 大文件")
        suggested.append("保留工具失败信息，或在恢复后重新采集大文件证据。")
    elif files and len(files) >= 2:
        mentioned_files = sum(1 for item in files[:2] if item.get("path") and str(item["path"]) in report)
        if mentioned_files < 2:
            findings.append("报告没有覆盖至少 2 个 Top 大文件证据。")
            missing.append("Top 大文件")
            suggested.append("把 list_large_files 的前 2 个文件写入报告。")

    build_cache = docker.get("build_cache_gb")
    images_gb = docker.get("images_gb")
    if docker.get("ok") is False:
        if "Docker 占用工具调用失败" not in report and "工具调用失败" not in report:
            findings.append("Docker 工具失败时，报告没有明确说明失败原因。")
            suggested.append("在 Docker 占用情况段落中保留 Docker 工具失败信息。")
    elif build_cache is None and images_gb is None:
        findings.append("Docker 占用没有具体 GB 数值。")
        missing.append("Docker 占用")
        suggested.append("补充 query_docker_disk_usage 的结构化返回。")
    elif (
        (build_cache is not None and _format_number(build_cache, "GB") not in report)
        and (images_gb is not None and _format_number(images_gb, "GB") not in report)
    ):
        findings.append("报告没有引用 Docker build cache 或 images 的具体 GB 数值。")
        missing.append("Docker 占用")
        suggested.append("在报告中明确写出 build cache 或 images 的占用值。")

    if not _is_remote_realtime_mode(evidence):
        safe_items = cleanup.get("safe", [])
        approval_items = cleanup.get("need_approval", [])
        forbidden_items = cleanup.get("forbidden", [])
        if not safe_items or not approval_items or not forbidden_items:
            findings.append("cleanup_candidates 的 safe / need_approval / forbidden 证据不完整。")
            missing.append("cleanup_candidates")
            suggested.append("补充 get_disk_cleanup_candidates 结果。")

    if deleted_result.get("ok") is False and "工具调用失败" not in report:
        findings.append("deleted open files 工具失败时，报告没有明确说明失败原因。")
        suggested.append("在 Deleted Open Files 段落中保留工具失败信息。")

    if "没有执行任何删除操作" not in report:
        findings.append("报告没有明确声明未执行任何删除操作。")
        warnings.append("缺少操作边界声明。")
        suggested.append("在风险提示中补充“没有执行任何删除操作”。")

    return findings, suggested, missing, warnings
