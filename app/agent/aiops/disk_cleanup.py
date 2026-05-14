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
    "get_disk_usage": {"hostname": "demo-server-01", "mount": "/"},
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


def _extract_embedded_json(text: str) -> Any:
    text_index = text.find('"text"')
    if text_index >= 0:
        colon_index = text.find(":", text_index)
        quote_index = text.find('"', colon_index + 1)
        if colon_index >= 0 and quote_index >= 0:
            chars: list[str] = []
            escaped = False
            for char in text[quote_index + 1 :]:
                if escaped:
                    chars.append(char)
                    escaped = False
                    continue
                if char == "\\":
                    chars.append(char)
                    escaped = True
                    continue
                if char == '"':
                    break
                chars.append(char)
            candidate = "".join(chars).replace('\\"', '"')
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if brace_start >= 0 and brace_end > brace_start:
        candidate = text[brace_start : brace_end + 1].replace('\\"', '"')
        candidate = candidate.rstrip('"}] ')
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None
    return None


def is_disk_cleanup_skill(skill: dict[str, Any] | str) -> bool:
    if isinstance(skill, dict):
        return skill.get("name") == DISK_SKILL_NAME
    return str(skill) == DISK_SKILL_NAME


def is_disk_cleanup_request(input_text: str, matched_skills: list[dict[str, Any]] | None = None) -> bool:
    normalized = (input_text or "").lower()
    if any(is_disk_cleanup_skill(skill) for skill in matched_skills or []):
        return True
    return any(keyword in normalized for keyword in DISK_KEYWORDS)


def build_disk_cleanup_plan() -> list[str]:
    return [
        "调用 get_disk_usage 获取 demo-server-01 主机根挂载点 / 的磁盘使用率证据。",
        "调用 list_large_directories 获取 / 下的高占用目录排行，定位 Top 目录占用。",
        "调用 list_large_files 获取 / 下的大文件清单，定位最占空间的日志和缓存文件。",
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
        "/var/log": "业务日志与归档日志堆积",
        "/var/lib/docker": "Docker 镜像、卷或构建缓存占用",
        "/tmp": "临时文件未定期清理",
        "/app/cache": "应用缓存未过期或未淘汰",
    }
    return mapping.get(path, "目录占用偏高，需要进一步核查内容组成")


def _file_safe_action(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".log"):
        return "先确认日志保留策略，再执行轮转、压缩或归档"
    if "cache" in lowered:
        return "确认不再被使用后再清理缓存文件"
    return "需要结合业务影响评估后再处理"


def _file_risk(path: str) -> str:
    lowered = path.lower()
    if lowered.endswith(".log"):
        return "直接删除可能影响审计、排障或业务写入"
    if "docker" in lowered:
        return "误删可能影响镜像层、构建缓存或容器运行"
    return "需要确认文件是否被在线业务依赖"


def _deleted_open_suggestion(process_name: str) -> str:
    if process_name:
        return f"在业务低峰平滑重启 {process_name}，释放已删除但未归还的磁盘空间"
    return "确认句柄所属进程后再安排平滑重启释放空间"


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
            "host": data.get("host") or "demo-server-01",
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
        return {
            "ok": True,
            "path": data.get("path") or "/",
            "limit": int(data.get("limit") or len(normalized) or 10),
            "directories": normalized,
            "source": data.get("source") or "mock",
        }

    if tool_name == "list_large_files":
        data = payload if isinstance(payload, dict) else {}
        files = data.get("files") if isinstance(data.get("files"), list) else []
        normalized = []
        for item in files:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path") or "")
            normalized.append(
                {
                    "path": path,
                    "size_gb": _to_float(item.get("size_gb")),
                    "safe_action": item.get("safe_action") or _file_safe_action(path),
                    "risk": item.get("risk") or _file_risk(path),
                }
            )
        return {
            "path": data.get("path") or "/",
            "min_size_mb": int(data.get("min_size_mb") or 100),
            "limit": int(data.get("limit") or len(normalized) or 20),
            "files": normalized,
        }

    if tool_name == "query_deleted_open_files":
        data = payload if isinstance(payload, dict) else {}
        files = data.get("files") if isinstance(data.get("files"), list) else []
        normalized = []
        for item in files:
            if not isinstance(item, dict):
                continue
            process_name = str(item.get("process") or item.get("process_name") or "")
            file_path = str(item.get("file") or item.get("path") or "")
            normalized.append(
                {
                    "process": process_name,
                    "pid": item.get("pid"),
                    "file": file_path,
                    "state": item.get("state") or "deleted",
                    "size_gb": _to_float(item.get("size_gb")),
                    "suggestion": item.get("suggestion") or _deleted_open_suggestion(process_name),
                }
            )
        return {
            "files": normalized,
            "total": int(data.get("total") or len(normalized)),
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
                    normalized_item["reason"] = item.get("reason") or "高风险或禁止自动执行"
                else:
                    normalized_item["suggestion"] = item.get("suggestion") or "需要人工确认后再执行"
                normalized_items.append(normalized_item)
            return normalized_items

        return {
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
        return payload[:280] if payload else "知识库未返回内容"
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
        return _join_or_missing(lines, "目录占用结果为空")

    if tool_name == "list_large_files":
        files = result.get("files", [])
        lines = [
            f"{item.get('path')}: {_format_number(item.get('size_gb'), 'GB')} | action={item.get('safe_action')} | risk={item.get('risk')}"
            for item in files[:5]
        ]
        return _join_or_missing(lines, "大文件结果为空")

    if tool_name == "query_deleted_open_files":
        files = result.get("files", [])
        lines = [
            f"{item.get('process')} pid={item.get('pid')} file={item.get('file')} size={_format_number(item.get('size_gb'), 'GB')} | {item.get('suggestion')}"
            for item in files[:5]
        ]
        return _join_or_missing(lines, "未发现 deleted open files")

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


def _field_or_missing(value: Any, suffix: str = "") -> str:
    if value is None or value == "":
        return "该字段未返回"
    return _format_number(value, suffix) if isinstance(value, (int, float)) else f"{value}{suffix}"


def build_disk_cleanup_report(input_text: str, past_steps: list[tuple[str, str]]) -> str:
    evidence = parse_disk_step_results(past_steps)
    disk_usage = evidence.get("get_disk_usage", {})
    directories = list(evidence.get("list_large_directories", {}).get("directories", []))
    files = list(evidence.get("list_large_files", {}).get("files", []))
    deleted_open_files = list(evidence.get("query_deleted_open_files", {}).get("files", []))
    docker_usage = evidence.get("query_docker_disk_usage", {})
    cleanup_candidates = evidence.get("get_disk_cleanup_candidates", {})
    knowledge = evidence.get("retrieve_knowledge", "")

    host = disk_usage.get("host") or "demo-server-01"
    mount = disk_usage.get("mount") or "/"
    usage_percent = _format_percent(disk_usage.get("usage_percent"))
    used_gb = _format_number(disk_usage.get("used_gb"), "GB")
    total_gb = _format_number(disk_usage.get("total_gb"), "GB")
    available_gb = _format_number(disk_usage.get("available_gb"), "GB")

    top_directories = _evidence_list_lines(
        directories[:5],
        lambda item: f"- `{item.get('path')}`: {_format_number(item.get('size_gb'), 'GB')}，原因：{item.get('reason') or '该字段未返回'}",
        "- 目录占用结果为空",
    )
    top_files = _evidence_list_lines(
        files[:5],
        lambda item: f"- `{item.get('path')}`: {_format_number(item.get('size_gb'), 'GB')}，建议：{item.get('safe_action') or '该字段未返回'}，风险：{item.get('risk') or '该字段未返回'}",
        "- 大文件结果为空",
    )
    deleted_files_text = _evidence_list_lines(
        deleted_open_files[:5],
        lambda item: (
            f"- 进程 `{item.get('process') or '该字段未返回'}` pid={item.get('pid') or '该字段未返回'}，"
            f"文件 `{item.get('file') or '该字段未返回'}`，占用 {_format_number(item.get('size_gb'), 'GB')}，"
            f"建议：{item.get('suggestion') or '该字段未返回'}"
        ),
        "- 未发现 deleted open files",
    )

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
        "- 可安全清理项为空",
    )
    approval_text = _evidence_list_lines(
        list(cleanup_candidates.get("need_approval", [])),
        lambda item: f"- {item.get('item')}: {_format_number(item.get('size_gb'), 'GB')}，需人工确认：{item.get('suggestion') or '该字段未返回'}",
        "- 需人工确认项为空",
    )
    forbidden_text = _evidence_list_lines(
        list(cleanup_candidates.get("forbidden", [])),
        lambda item: f"- {item.get('item')}：{item.get('reason') or '该字段未返回'}",
        "- 禁止自动清理项为空",
    )

    root_cause_lines = []
    if disk_usage.get("usage_percent") is not None:
        root_cause_lines.append(
            f"- 根挂载点 `{mount}` 当前磁盘使用率为 **{usage_percent}**，剩余可用空间仅 **{available_gb}**。"
        )
    else:
        root_cause_lines.append("- 磁盘使用率字段未返回，当前无法确认挂载点压力水位。")

    if directories:
        leading_dirs = "、".join(
            f"`{item.get('path')}`（{_format_number(item.get('size_gb'), 'GB')}）"
            for item in directories[:3]
        )
        root_cause_lines.append(f"- 主要目录压力来源集中在 {leading_dirs}。")
    else:
        root_cause_lines.append("- 目录占用结果为空，当前不能确认主要目录压力来源。")

    if files:
        leading_files = "、".join(
            f"`{item.get('path')}`（{_format_number(item.get('size_gb'), 'GB')}）"
            for item in files[:3]
        )
        root_cause_lines.append(f"- 主要大文件包括 {leading_files}。")
    else:
        root_cause_lines.append("- 大文件结果为空，当前不能确认具体的大文件压力来源。")

    if deleted_open_files:
        first_deleted = deleted_open_files[0]
        root_cause_lines.append(
            f"- 发现 deleted open file：`{first_deleted.get('file')}` 仍被 `{first_deleted.get('process')}` 持有，占用 {_format_number(first_deleted.get('size_gb'), 'GB')}。"
        )
    else:
        root_cause_lines.append("- 未发现 deleted open files。")

    if docker_usage:
        root_cause_lines.append(
            f"- Docker 占用总量为 {_format_number(docker_usage.get('total_gb'), 'GB')}，其中 build cache 为 {_format_number(docker_usage.get('build_cache_gb'), 'GB')}。"
        )
    else:
        root_cause_lines.append("- Docker 占用字段未返回，当前不能评估容器侧磁盘压力。")

    key_evidence_lines = []
    if disk_usage.get("usage_percent") is not None:
        key_evidence_lines.append(f"- 磁盘使用率：**{usage_percent}**")
    else:
        key_evidence_lines.append("- 磁盘使用率：该字段未返回")

    if directories:
        for item in directories[:2]:
            key_evidence_lines.append(f"- 目录 `{item.get('path')}`：**{_format_number(item.get('size_gb'), 'GB')}**")
    else:
        key_evidence_lines.append("- Top 目录：结果为空")

    if files:
        for item in files[:2]:
            key_evidence_lines.append(f"- 文件 `{item.get('path')}`：**{_format_number(item.get('size_gb'), 'GB')}**")
    else:
        key_evidence_lines.append("- Top 大文件：结果为空")

    key_evidence_lines.append(f"- Docker build cache：**{_format_number(docker_usage.get('build_cache_gb'), 'GB')}**")

    report = dedent(
        f"""
        # AIOps 磁盘清理诊断报告

        ## 用户任务
        - {input_text}

        ## 磁盘使用率证据
        - 主机 `{host}` 的挂载点 `{mount}` 当前使用率为 **{usage_percent}**。
        - 已使用 **{used_gb} / {total_gb}**，剩余可用空间 **{available_gb}**。
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
        - 受影响主机：`{host}`
        - 风险表现：磁盘接近写满时，可能引发日志写入失败、任务落盘失败、容器调度失败或业务缓存写入异常。
        - 本次结论仅基于 mock 现场数据、知识库 runbook 和工具回传结果，不代表已执行任何清理动作。

        ## 可安全清理项
        {safe_text}

        ## 需人工确认项
        {approval_text}

        ## 禁止自动清理项
        {forbidden_text}

        ## 风险提示
        - 本次诊断 **没有执行任何删除操作**，所有删除类命令都只作为人工确认后的建议展示。
        - `rm -rf`、`docker system prune --volumes`、删除数据库目录、删除业务 `uploads/`、删除 Milvus/MinIO/etcd volumes 都必须视为高风险或禁止自动执行。
        - 对 deleted open files，只能在确认业务可重启或可平滑滚动的前提下，通过重启相关进程释放空间。

        ## 后续预防措施
        - 为业务日志设置轮转、压缩和保留上限，避免单个日志文件持续膨胀。
        - 定期审计 `/var/lib/docker` 下的镜像、卷和 build cache，占满前提前清理。
        - 为根分区设置 80% / 90% 阈值告警，并将 `HighDiskUsage` / `DiskFull` 关联到对应 runbook。
        - 将 deleted open files 检查纳入常规巡检，避免“文件已删但空间未释放”的隐性泄漏。

        ## Runbook 参考
        - aiops-docs 中上传的 runbook 仅作为知识库参考，不是实时日志。
        - 检索关键词：`{DISK_KNOWLEDGE_QUERY}`
        - 本次参考摘要：{_short_knowledge_summary(knowledge)}
        """
    ).strip()
    return report


def build_disk_verifier_findings(report: str, past_steps: list[tuple[str, str]]) -> tuple[list[str], list[str], list[str], list[str]]:
    """Hard verification rules for disk-cleanup reports."""
    evidence = parse_disk_step_results(past_steps)
    directories = list(evidence.get("list_large_directories", {}).get("directories", []))
    files = list(evidence.get("list_large_files", {}).get("files", []))
    docker = evidence.get("query_docker_disk_usage", {})
    cleanup = evidence.get("get_disk_cleanup_candidates", {})
    disk_usage = evidence.get("get_disk_usage", {})

    findings: list[str] = []
    suggested: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []
    lowered = report.lower()

    if "unknown" in lowered:
        findings.append("报告中包含 unknown 占位，说明字段解析或报告生成仍不完整。")
        suggested.append("重新解析磁盘工具结果并基于结构化字段重建报告。")

    if disk_usage.get("usage_percent") is None or not re.search(r"\b\d+(?:\.\d+)?%", report):
        findings.append("报告缺少具体磁盘使用率百分比。")
        missing.append("磁盘使用率百分比")
        suggested.append("重新检查 get_disk_usage 返回值并补充 usage_percent。")

    if directories and len(directories) >= 2:
        mentioned_dirs = sum(1 for item in directories[:2] if item.get("path") and str(item["path"]) in report)
        if mentioned_dirs < 2:
            findings.append("报告没有完整体现至少 2 个 Top 目录证据。")
            missing.append("Top 目录证据")
            suggested.append("在报告中补充至少两个目录路径与占用值。")
    else:
        findings.append("目录占用证据不足，未达到至少 2 个 Top 目录的最低标准。")
        missing.append("Top 目录证据")
        suggested.append("重新执行 list_large_directories 并确认返回至少两个目录。")

    if files and len(files) >= 2:
        mentioned_files = sum(1 for item in files[:2] if item.get("path") and str(item["path"]) in report)
        if mentioned_files < 2:
            findings.append("报告没有完整体现至少 2 个 Top 大文件证据。")
            missing.append("Top 大文件证据")
            suggested.append("在报告中补充至少两个大文件路径与占用值。")
    else:
        findings.append("大文件证据不足，未达到至少 2 个 Top 大文件的最低标准。")
        missing.append("Top 大文件证据")
        suggested.append("重新执行 list_large_files 并确认返回至少两个大文件。")

    if "未采集到目录占用数据" in report and any(item.get("path") and str(item["path"]) in report for item in directories):
        findings.append("报告声称未采集到目录占用数据，但根因分析仍引用了具体目录。")
        suggested.append("修正目录占用段落，避免在无证据时引用具体目录。")

    if "未采集到大文件数据" in report and any(item.get("path") and str(item["path"]) in report for item in files):
        findings.append("报告声称未采集到大文件数据，但根因分析仍引用了具体文件。")
        suggested.append("修正大文件段落，避免在无证据时引用具体文件。")

    build_cache = docker.get("build_cache_gb")
    images_gb = docker.get("images_gb")
    if build_cache is None and images_gb is None:
        findings.append("Docker 占用缺少具体 GB 数值。")
        missing.append("Docker 占用")
        suggested.append("重新检查 query_docker_disk_usage 的结构化结果。")
    elif (
        (build_cache is not None and _format_number(build_cache, "GB") not in report)
        and (images_gb is not None and _format_number(images_gb, "GB") not in report)
    ):
        findings.append("报告没有体现 Docker build cache 或 images 的具体 GB 数值。")
        missing.append("Docker 占用")
        suggested.append("在报告中补充 Docker images 或 build cache 的具体占用值。")

    safe_items = cleanup.get("safe", [])
    approval_items = cleanup.get("need_approval", [])
    forbidden_items = cleanup.get("forbidden", [])
    if not safe_items or not approval_items or not forbidden_items:
        findings.append("清理候选项不完整，safe / need_approval / forbidden 至少有一类为空。")
        missing.append("cleanup_candidates")
        suggested.append("重新检查 get_disk_cleanup_candidates 的返回结构。")

    if "没有执行任何删除操作" not in report:
        findings.append("报告没有明确说明本次未执行任何删除操作。")
        warnings.append("必须显式声明未执行删除操作，避免误导。")
        suggested.append("在风险提示中明确写明未执行任何删除操作。")

    return findings, suggested, missing, warnings
