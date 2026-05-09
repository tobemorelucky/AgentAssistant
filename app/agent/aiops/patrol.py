"""Controlled default patrol planning and evidence helpers."""

from __future__ import annotations

import json
import time
from textwrap import dedent
from typing import Any

from app.agent.aiops.disk_cleanup import unwrap_structured_payload
from app.models.aiops import ToolPlanStep


SEVERITY_ORDER = {"critical": 4, "high": 3, "warning": 2, "info": 1, "low": 1}

ALERT_EVIDENCE_RULES: dict[str, dict[str, Any]] = {
    "HighCPUUsage": {
        "required_evidence": {
            "metric": ["query_cpu_metrics"],
            "process": ["query_process_list"],
            "log": ["search_log"],
            "historical": ["search_historical_tickets"],
            "runbook": ["retrieve_knowledge"],
        },
        "helper_tools": ["get_service_info", "search_topic_by_service_name"],
    },
    "HighDiskUsage": {
        "required_evidence": {
            "disk_usage": ["get_disk_usage"],
            "directory": ["list_large_directories"],
            "file": ["list_large_files"],
            "docker": ["query_docker_disk_usage"],
            "runbook": ["retrieve_knowledge"],
        },
        "helper_tools": ["query_deleted_open_files", "get_disk_cleanup_candidates"],
    },
    "DiskFull": {
        "required_evidence": {
            "disk_usage": ["get_disk_usage"],
            "directory": ["list_large_directories"],
            "file": ["list_large_files"],
            "docker": ["query_docker_disk_usage"],
            "runbook": ["retrieve_knowledge"],
        },
        "helper_tools": ["query_deleted_open_files", "get_disk_cleanup_candidates"],
    },
}


def choose_highest_severity_alert(alerts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not alerts:
        return None
    return sorted(
        alerts,
        key=lambda alert: (
            -SEVERITY_ORDER.get(str(alert.get("severity", "")).lower(), 0),
            str(alert.get("service_name", "")),
            str(alert.get("alert_name", "")),
        ),
    )[0]


def build_no_alert_report() -> str:
    return dedent(
        """
        # AIOps 默认巡检报告

        ## 巡检结论
        - 当前未检测到活跃告警。

        ## 执行轨迹
        - 已执行活跃告警发现。
        - mock 告警源未返回 firing 状态告警。

        ## 风险提示
        - 当前结论基于 mock 活跃告警数据。
        - 本次未触发任何高风险操作，也未执行任何删除或变更命令。

        ## 后续建议
        - 继续保留定时巡检。
        - 如需深入排查，可继续结合监控、日志和工单工具做专项分析。
        """
    ).strip()


def get_alert_rule(alert_name: str, matched_skills: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if alert_name in ALERT_EVIDENCE_RULES:
        return ALERT_EVIDENCE_RULES[alert_name]
    if any(skill.get("name") == "disk_cleanup" for skill in matched_skills or []):
        return ALERT_EVIDENCE_RULES["HighDiskUsage"]
    return ALERT_EVIDENCE_RULES["HighCPUUsage"]


def format_tool_plan_step(step: ToolPlanStep | dict[str, Any] | str) -> str:
    if isinstance(step, str):
        return step
    payload = step.model_dump() if isinstance(step, ToolPlanStep) else dict(step)
    tool = payload.get("tool", "unknown_tool")
    evidence_type = payload.get("evidence_type", "evidence")
    reason = payload.get("reason", "")
    args = payload.get("args", {}) or {}
    args_preview = ", ".join(f"{key}={value}" for key, value in list(args.items())[:4])
    suffix = f" ({args_preview})" if args_preview else ""
    return f"[{evidence_type}] {tool}{suffix} - {reason}".strip()


def parse_tool_plan_step(step: Any) -> ToolPlanStep | None:
    if isinstance(step, ToolPlanStep):
        return step
    if isinstance(step, dict) and {"tool", "args", "reason", "evidence_type"} <= set(step.keys()):
        try:
            return ToolPlanStep(**step)
        except Exception:
            return None
    return None


def build_fallback_tool_plan(
    target_alert: dict[str, Any],
    matched_skills: list[dict[str, Any]] | None = None,
) -> list[ToolPlanStep]:
    service_name = target_alert.get("service_name", "unknown-service")
    alert_name = target_alert.get("alert_name", "unknown-alert")

    if alert_name in {"HighDiskUsage", "DiskFull"} or any(
        skill.get("name") == "disk_cleanup" for skill in matched_skills or []
    ):
        return [
            ToolPlanStep(
                tool="get_disk_usage",
                args={"hostname": "demo-server-01", "mount": "/"},
                reason="确认根分区磁盘使用率是否已达到告警阈值。",
                evidence_type="disk_usage",
            ),
            ToolPlanStep(
                tool="list_large_directories",
                args={"path": "/", "limit": 10},
                reason="定位占用空间最高的目录。",
                evidence_type="directory",
            ),
            ToolPlanStep(
                tool="list_large_files",
                args={"path": "/", "min_size_mb": 100, "limit": 20},
                reason="定位最大的文件和清理候选。",
                evidence_type="file",
            ),
            ToolPlanStep(
                tool="query_deleted_open_files",
                args={},
                reason="确认是否存在已删除但仍占用磁盘空间的文件句柄。",
                evidence_type="deleted_open_files",
            ),
            ToolPlanStep(
                tool="query_docker_disk_usage",
                args={},
                reason="确认 Docker 镜像、卷和构建缓存占用。",
                evidence_type="docker",
            ),
            ToolPlanStep(
                tool="get_disk_cleanup_candidates",
                args={},
                reason="提取可安全清理、需审批和禁止自动清理的项目。",
                evidence_type="cleanup_candidates",
            ),
            ToolPlanStep(
                tool="retrieve_knowledge",
                args={"query": "磁盘使用率过高 清理 runbook"},
                reason="补充 runbook 中的标准清理流程和风险提示。",
                evidence_type="runbook",
            ),
        ]

    return [
        ToolPlanStep(
            tool="get_service_info",
            args={"service_name": service_name},
            reason="确认服务 owner、部署形态和依赖上下文。",
            evidence_type="service_context",
        ),
        ToolPlanStep(
            tool="query_cpu_metrics",
            args={"service_name": service_name, "interval": "1m"},
            reason="收集 CPU 指标证据，验证 HighCPUUsage 告警。",
            evidence_type="metric",
        ),
        ToolPlanStep(
            tool="query_process_list",
            args={"service_name": service_name},
            reason="识别 CPU 热点进程和实例。",
            evidence_type="process",
        ),
        ToolPlanStep(
            tool="search_topic_by_service_name",
            args={"service_name": service_name, "fuzzy": False},
            reason="定位服务日志 topic，为日志证据做准备。",
            evidence_type="log_locator",
        ),
        ToolPlanStep(
            tool="search_log",
            args={"query": f"{service_name} {alert_name}", "limit": 100, "window_minutes": 15},
            reason="检索最近日志，确认是否有超时、重试或异常堆积。",
            evidence_type="log",
        ),
        ToolPlanStep(
            tool="search_historical_tickets",
            args={"service_name": service_name, "alert_name": alert_name, "limit": 5},
            reason="比对历史工单中的根因和处理经验。",
            evidence_type="historical",
        ),
        ToolPlanStep(
            tool="retrieve_knowledge",
            args={"query": f"{service_name} {alert_name} runbook"},
            reason="补充 runbook 建议和风险提示。",
            evidence_type="runbook",
        ),
    ]


def build_web_search_step(target_alert: dict[str, Any]) -> ToolPlanStep:
    service_name = target_alert.get("service_name", "unknown-service")
    alert_name = target_alert.get("alert_name", "unknown-alert")
    return ToolPlanStep(
        tool="web_search",
        args={"query": f"{service_name} {alert_name} official documentation troubleshooting"},
        reason="补充外部公开文档、错误码说明或官方排障资料。",
        evidence_type="external_reference",
    )


def sanitize_tool_plan_steps(
    raw_steps: list[ToolPlanStep | dict[str, Any]],
    *,
    target_alert: dict[str, Any],
    matched_skills: list[dict[str, Any]] | None,
    available_tools: set[str],
    blocked_tools: set[str],
) -> list[ToolPlanStep]:
    alert_name = target_alert.get("alert_name", "unknown-alert")
    service_name = target_alert.get("service_name", "unknown-service")
    fallback_steps = build_fallback_tool_plan(target_alert, matched_skills)
    fallback_by_evidence = {step.evidence_type: step for step in fallback_steps}
    fallback_by_tool = {step.tool: step for step in fallback_steps}
    rule = get_alert_rule(alert_name, matched_skills)

    sanitized: list[ToolPlanStep] = []
    seen_signatures: set[str] = set()

    def add_step(step: ToolPlanStep) -> None:
        if step.tool not in available_tools or step.tool in blocked_tools:
            return
        signature = json.dumps(step.model_dump(), ensure_ascii=False, sort_keys=True)
        if signature in seen_signatures:
            return
        seen_signatures.add(signature)
        sanitized.append(step)

    for raw_step in raw_steps[:8]:
        step = parse_tool_plan_step(raw_step)
        if not step or step.tool not in available_tools or step.tool in blocked_tools:
            continue

        args = dict(step.args or {})
        if step.tool in {
            "query_cpu_metrics",
            "query_memory_metrics",
            "query_process_list",
            "get_service_info",
            "search_historical_tickets",
        }:
            args.setdefault("service_name", service_name)
        if step.tool == "search_historical_tickets":
            args.setdefault("alert_name", alert_name)
            args.setdefault("limit", 5)
        if step.tool == "retrieve_knowledge":
            args.setdefault(
                "query",
                "磁盘使用率过高 清理 runbook"
                if alert_name in {"HighDiskUsage", "DiskFull"}
                else f"{service_name} {alert_name} runbook",
            )
        if step.tool == "search_topic_by_service_name":
            args.setdefault("service_name", service_name)
            args.setdefault("fuzzy", False)
        if step.tool == "search_log":
            args.setdefault("query", f"{service_name} {alert_name}")
            args.setdefault("limit", 100)
            args.setdefault("window_minutes", 15)
        if step.tool == "get_disk_usage":
            args.setdefault("hostname", "demo-server-01")
            args.setdefault("mount", "/")
        if step.tool == "list_large_directories":
            args.setdefault("path", "/")
            args.setdefault("limit", 10)
        if step.tool == "list_large_files":
            args.setdefault("path", "/")
            args.setdefault("min_size_mb", 100)
            args.setdefault("limit", 20)
        add_step(ToolPlanStep(tool=step.tool, args=args, reason=step.reason, evidence_type=step.evidence_type))

    for evidence_type, required_tools in rule["required_evidence"].items():
        if any(step.evidence_type == evidence_type for step in sanitized):
            continue
        for tool_name in required_tools:
            template = fallback_by_tool.get(tool_name) or fallback_by_evidence.get(evidence_type)
            if template:
                add_step(template)
                break

    if any(step.tool == "search_log" for step in sanitized) and not any(
        step.tool == "search_topic_by_service_name" for step in sanitized
    ):
        helper = fallback_by_tool.get("search_topic_by_service_name")
        if helper:
            add_step(helper)

    if not sanitized:
        for step in fallback_steps:
            add_step(step)

    return _stable_order_steps(sanitized)[:8]


def _stable_order_steps(steps: list[ToolPlanStep]) -> list[ToolPlanStep]:
    priority = {
        "service_context": 5,
        "metric": 10,
        "disk_usage": 10,
        "process": 20,
        "directory": 20,
        "file": 30,
        "deleted_open_files": 35,
        "log_locator": 40,
        "log": 45,
        "docker": 50,
        "historical": 60,
        "cleanup_candidates": 65,
        "runbook": 70,
    }
    return sorted(steps, key=lambda step: priority.get(step.evidence_type, 99))


def tool_plan_steps_to_dicts(steps: list[ToolPlanStep]) -> list[dict[str, Any]]:
    return [step.model_dump() for step in steps]


def summarize_alerts(alerts: list[dict[str, Any]]) -> str:
    if not alerts:
        return "No active alerts."
    return "; ".join(
        f"{alert.get('service_name', 'unknown')}/{alert.get('alert_name', 'unknown')} [{alert.get('severity', 'unknown')}]"
        for alert in alerts[:5]
    )


def required_evidence_summary(
    target_alert: dict[str, Any],
    matched_skills: list[dict[str, Any]] | None = None,
) -> dict[str, list[str]]:
    return get_alert_rule(target_alert.get("alert_name", ""), matched_skills).get("required_evidence", {})


def step_label_from_plan(step: ToolPlanStep | dict[str, Any]) -> str:
    return format_tool_plan_step(step)


def extract_tool_name_from_step_label(step_label: str) -> str | None:
    if not step_label:
        return None
    if "] " in step_label and " - " in step_label:
        fragment = step_label.split("] ", 1)[1].split(" - ", 1)[0]
        return fragment.split("(", 1)[0].strip()
    return None


def parse_tool_results_from_history(past_steps: list[tuple[str, str]]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for step_label, result in past_steps:
        tool_name = extract_tool_name_from_step_label(step_label)
        if not tool_name:
            continue
        evidence[tool_name] = unwrap_structured_payload(result)
    return evidence


def _is_usable_evidence(payload: Any) -> bool:
    if payload in (None, "", [], {}):
        return False
    if isinstance(payload, dict) and payload.get("error"):
        return False
    return True


def summarize_structured_tool_result(tool_name: str, result: Any) -> str:
    payload = unwrap_structured_payload(result)
    if isinstance(payload, dict) and payload.get("error"):
        return f"执行失败: {payload.get('error')}"
    if tool_name == "get_active_alerts" and isinstance(payload, dict):
        alerts = payload.get("active_alerts", []) or payload.get("alerts", []) or []
        return f"active_alerts={len(alerts)}"
    if tool_name == "query_cpu_metrics" and isinstance(payload, dict):
        stats = payload.get("statistics", {}) or {}
        return (
            f"service={payload.get('service_name')}, avg={stats.get('avg')}%, "
            f"max={stats.get('max')}%, spike_detected={stats.get('spike_detected')}"
        )
    if tool_name == "query_memory_metrics" and isinstance(payload, dict):
        stats = payload.get("statistics", {}) or {}
        return f"service={payload.get('service_name')}, avg={stats.get('avg')}%, max={stats.get('max')}%"
    if tool_name == "query_process_list" and isinstance(payload, dict):
        processes = payload.get("processes", []) or []
        if processes:
            top = processes[0]
            return (
                f"{top.get('instance')} pid={top.get('pid')} "
                f"cpu={top.get('cpu_percent')}% mem={top.get('memory_percent')}%"
            )
        return "未返回进程数据"
    if tool_name == "search_topic_by_service_name" and isinstance(payload, dict):
        topics = payload.get("topics", []) or []
        preview = ", ".join(topic.get("topic_id", "") for topic in topics[:3] if isinstance(topic, dict))
        return f"topics={len(topics)} {preview}".strip()
    if tool_name == "search_log" and isinstance(payload, dict):
        logs = payload.get("logs", []) or []
        first = logs[0] if logs else {}
        if isinstance(first, dict):
            return f"logs={len(logs)} first={first.get('level')} {str(first.get('message', ''))[:80]}"
        return f"logs={len(logs)}"
    if tool_name == "search_historical_tickets" and isinstance(payload, dict):
        tickets = payload.get("tickets", []) or []
        preview = ", ".join(ticket.get("ticket_id", "") for ticket in tickets[:3] if isinstance(ticket, dict))
        return f"tickets={len(tickets)} {preview}".strip()
    if tool_name == "get_service_info" and isinstance(payload, dict):
        return (
            f"service={payload.get('service_name')} owner={payload.get('owner_team')} "
            f"deployment={payload.get('deployment')}"
        )
    if tool_name == "retrieve_knowledge":
        if isinstance(payload, dict):
            return str(payload.get("content", ""))[:220]
        return str(payload)[:220]
    if tool_name == "web_search" and isinstance(payload, dict):
        content = str(payload.get("content", "")).strip()
        artifacts = payload.get("artifacts", []) or []
        first = artifacts[0] if artifacts else {}
        if isinstance(first, dict):
            title = (first.get("metadata") or {}).get("title", "")
            source = (first.get("metadata") or {}).get("source", "")
            return f"results={len(artifacts)} first={title} {source}".strip()[:220]
        return content[:220]
    if isinstance(payload, dict):
        preferred = {k: v for k, v in payload.items() if k not in {"type", "data", "test"}}
        return json.dumps(preferred, ensure_ascii=False)[:220]
    if isinstance(payload, list):
        return f"items={len(payload)}"
    return str(payload)[:220]


def resolve_structured_step_args(
    step: ToolPlanStep,
    *,
    state: dict[str, Any],
    previous_results: dict[str, Any],
) -> dict[str, Any]:
    args = dict(step.args or {})
    target_alert = state.get("target_alert") or {}
    service_name = target_alert.get("service_name")
    alert_name = target_alert.get("alert_name")

    if step.tool in {"query_cpu_metrics", "query_memory_metrics", "query_process_list", "get_service_info"}:
        if service_name:
            args.setdefault("service_name", service_name)

    if step.tool == "search_historical_tickets":
        if service_name:
            args.setdefault("service_name", service_name)
        if alert_name:
            args.setdefault("alert_name", alert_name)
        args.setdefault("limit", 5)

    if step.tool == "retrieve_knowledge":
        if alert_name in {"HighDiskUsage", "DiskFull"}:
            args.setdefault("query", "磁盘使用率过高 清理 runbook")
        elif service_name and alert_name:
            args.setdefault("query", f"{service_name} {alert_name} runbook")

    if step.tool == "search_topic_by_service_name":
        if service_name:
            args.setdefault("service_name", service_name)
        args.setdefault("fuzzy", False)

    if step.tool == "search_log":
        topic_lookup = previous_results.get("search_topic_by_service_name", {})
        topics = topic_lookup.get("topics", []) if isinstance(topic_lookup, dict) else []
        if topics and isinstance(topics[0], dict):
            args.setdefault("topic_id", topics[0].get("topic_id"))
        end_time = int(time.time() * 1000)
        window_minutes = int(args.pop("window_minutes", 15))
        args.setdefault("end_time", end_time)
        args.setdefault("start_time", end_time - window_minutes * 60 * 1000)
        if service_name and alert_name:
            args.setdefault("query", f"{service_name} {alert_name}")
        args.setdefault("limit", 100)

    if step.tool == "get_disk_usage":
        args.setdefault("hostname", "demo-server-01")
        args.setdefault("mount", "/")
    if step.tool == "list_large_directories":
        args.setdefault("path", "/")
        args.setdefault("limit", 10)
    if step.tool == "list_large_files":
        args.setdefault("path", "/")
        args.setdefault("min_size_mb", 100)
        args.setdefault("limit", 20)

    return args


def collect_evidence_gaps(
    *,
    target_alert: dict[str, Any] | None,
    matched_skills: list[dict[str, Any]] | None,
    past_steps: list[tuple[str, str]],
    available_tools: set[str],
    blocked_tools: set[str],
) -> list[ToolPlanStep]:
    if not target_alert:
        return []
    required = required_evidence_summary(target_alert, matched_skills)
    collected = parse_tool_results_from_history(past_steps)
    fallback_steps = build_fallback_tool_plan(target_alert, matched_skills)
    gaps: list[ToolPlanStep] = []

    for evidence_type, required_tools in required.items():
        if any(_is_usable_evidence(collected.get(tool_name)) for tool_name in required_tools):
            continue
        candidate = next(
            (
                step
                for step in fallback_steps
                if step.evidence_type == evidence_type
                and step.tool in available_tools
                and step.tool not in blocked_tools
            ),
            None,
        )
        if candidate:
            gaps.append(candidate)

    if any(step.tool == "search_log" for step in gaps) and not _is_usable_evidence(
        collected.get("search_topic_by_service_name")
    ):
        helper = next((step for step in fallback_steps if step.tool == "search_topic_by_service_name"), None)
        if helper and helper.tool in available_tools and helper.tool not in blocked_tools:
            gaps.insert(0, helper)
    knowledge_payload = collected.get("retrieve_knowledge")
    knowledge_missing = not _is_usable_evidence(knowledge_payload)
    if isinstance(knowledge_payload, dict):
        knowledge_missing = not str(knowledge_payload.get("content", "")).strip()
    if knowledge_missing and "web_search" in available_tools and "web_search" not in blocked_tools:
        if not _is_usable_evidence(collected.get("web_search")):
            gaps.append(build_web_search_step(target_alert))
    return _stable_order_steps(gaps)


def build_alert_report(input_text: str, target_alert: dict[str, Any], past_steps: list[tuple[str, str]]) -> str:
    evidence = parse_tool_results_from_history(past_steps)
    service_name = target_alert.get("service_name", "unknown-service")
    alert_name = target_alert.get("alert_name", "unknown-alert")
    severity = target_alert.get("severity", "unknown")
    instance = target_alert.get("instance", "unknown-instance")
    duration = target_alert.get("duration", "unknown")

    web_search_payload = evidence.get("web_search") if isinstance(evidence.get("web_search"), dict) else {}
    web_search_docs = web_search_payload.get("artifacts", []) or []
    web_search_section = ""
    if web_search_docs:
        lines = []
        for item in web_search_docs[:3]:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") or {}
            lines.append(
                "- 标题: {title}\n  链接: {source}\n  摘要: {summary}\n  用途: 用于补充 Runbook / 官方说明 / 错误码解释，不作为本地故障直接证据".format(
                    title=metadata.get("title") or "未提供标题",
                    source=metadata.get("source") or "未提供链接",
                    summary=str(item.get("page_content") or "")[:180] or "未提供摘要",
                )
            )
        if lines:
            web_search_section = "\n\n## 联网搜索补充资料\n" + "\n".join(lines)

    if alert_name in {"HighDiskUsage", "DiskFull"}:
        disk_usage = evidence.get("get_disk_usage") if isinstance(evidence.get("get_disk_usage"), dict) else {}
        directories = (evidence.get("list_large_directories") or {}).get("directories", [])
        files = (evidence.get("list_large_files") or {}).get("files", [])
        deleted = (evidence.get("query_deleted_open_files") or {}).get("files", [])
        docker = evidence.get("query_docker_disk_usage") if isinstance(evidence.get("query_docker_disk_usage"), dict) else {}
        cleanup = evidence.get("get_disk_cleanup_candidates") if isinstance(evidence.get("get_disk_cleanup_candidates"), dict) else {}
        knowledge_payload = evidence.get("retrieve_knowledge", "")
        knowledge = (
            knowledge_payload.get("content", "")
            if isinstance(knowledge_payload, dict)
            else knowledge_payload
        )

        top_dirs = [
            f"- `{item.get('path')}`: {item.get('size_gb')}GB"
            for item in directories[:3]
            if isinstance(item, dict) and item.get("path")
        ] or ["- 该字段未返回"]
        top_files = [
            f"- `{item.get('path')}`: {item.get('size_gb')}GB"
            for item in files[:3]
            if isinstance(item, dict) and item.get("path")
        ] or ["- 该字段未返回"]
        deleted_lines = [
            f"- `{item.get('process_name') or item.get('process')}` pid={item.get('pid')} 持有 `{item.get('path') or item.get('file')}` {item.get('size_gb')}GB"
            for item in deleted[:3]
            if isinstance(item, dict)
        ] or ["- 该字段未返回"]
        safe_items = cleanup.get("safe", []) or []
        need_approval = cleanup.get("need_approval", []) or []
        forbidden = cleanup.get("forbidden", []) or []

        root_cause = []
        if directories:
            root_cause.append("目录占用证据显示磁盘压力主要集中在高占用目录。")
        if files:
            root_cause.append("大文件证据说明日志或缓存文件是主要容量来源之一。")
        if docker and any(docker.get(key) not in (None, "") for key in ("images_gb", "build_cache_gb")):
            root_cause.append("Docker 镜像或构建缓存也贡献了明显磁盘占用。")
        if not root_cause:
            root_cause.append("磁盘使用率已确认偏高，但当前仍缺少足够的目录/文件证据来锁定根因。")

        return dedent(
            f"""
            # AIOps 默认巡检报告

            ## 巡检任务
            - {input_text}

            ## 目标告警
            - 服务: `{service_name}`
            - 告警: `{alert_name}`
            - 严重级别: `{severity}`
            - 实例: `{instance}`
            - 持续时间: `{duration}`

            ## 关键证据
            - 磁盘使用率: {disk_usage.get('usage_percent', '该字段未返回')}%
            - 已用容量: {disk_usage.get('used_gb', '该字段未返回')}GB / {disk_usage.get('total_gb', '该字段未返回')}GB
            - 可用容量: {disk_usage.get('available_gb', '该字段未返回')}GB
            - Docker 占用: images={docker.get('images_gb', '该字段未返回')}GB, containers={docker.get('containers_gb', '该字段未返回')}GB, volumes={docker.get('volumes_gb', '该字段未返回')}GB, build_cache={docker.get('build_cache_gb', '该字段未返回')}GB

            ## Top 目录占用
            {chr(10).join(top_dirs)}

            ## Top 大文件
            {chr(10).join(top_files)}

            ## Deleted Open Files
            {chr(10).join(deleted_lines)}

            ## 根因分析
            {chr(10).join(f"- {item}" for item in root_cause)}

            ## 可安全清理项
            {chr(10).join(f"- {item}" for item in safe_items) if safe_items else "- 该字段未返回"}

            ## 需人工确认项
            {chr(10).join(f"- {item}" for item in need_approval) if need_approval else "- 该字段未返回"}

            ## 禁止自动清理项
            {chr(10).join(f"- {item}" for item in forbidden) if forbidden else "- 该字段未返回"}

            ## 风险提示
            - 本次仅采集 mock 只读证据，未执行任何删除操作。
            - `rm -rf`、`docker system prune --volumes`、删除数据库目录、删除业务 uploads、删除 Milvus/MinIO/etcd volumes 均属于高风险或禁止自动执行操作。

            ## Runbook 参考
            - {str(knowledge)[:220] if knowledge else "该字段未返回"}

            ## 后续预防措施
            - 为日志、缓存、Docker 构建缓存设置容量水位和自动轮转策略。
            - 为磁盘热点目录建立定期巡检与告警阈值。
            """
        ).strip() + web_search_section

    cpu_metrics = evidence.get("query_cpu_metrics") if isinstance(evidence.get("query_cpu_metrics"), dict) else {}
    process_list = evidence.get("query_process_list") if isinstance(evidence.get("query_process_list"), dict) else {}
    service_info = evidence.get("get_service_info") if isinstance(evidence.get("get_service_info"), dict) else {}
    ticket_info = evidence.get("search_historical_tickets") if isinstance(evidence.get("search_historical_tickets"), dict) else {}
    log_info = evidence.get("search_log") if isinstance(evidence.get("search_log"), dict) else {}
    knowledge_payload = evidence.get("retrieve_knowledge", "")
    knowledge = knowledge_payload.get("content", "") if isinstance(knowledge_payload, dict) else knowledge_payload

    cpu_stats = cpu_metrics.get("statistics", {}) or {}
    processes = process_list.get("processes", []) or []
    tickets = ticket_info.get("tickets", []) or []
    logs = log_info.get("logs", []) or []
    top_process = processes[0] if processes else {}
    first_log = logs[0] if logs else {}
    dependencies = ", ".join(service_info.get("dependencies", []) or []) or "该字段未返回"

    root_cause_lines: list[str] = []
    if cpu_stats.get("max") is not None:
        root_cause_lines.append(f"CPU 指标峰值达到 {cpu_stats.get('max')}%，说明告警与资源使用升高一致。")
    if top_process:
        root_cause_lines.append(
            f"热点进程为 `{top_process.get('instance')}` pid={top_process.get('pid')}，CPU={top_process.get('cpu_percent')}%。"
        )
    if first_log and first_log.get("message"):
        root_cause_lines.append(f"日志证据显示：{first_log.get('message')}")
    if tickets:
        root_cause_lines.append(f"历史工单 `{tickets[0].get('ticket_id')}` 的根因记录为：{tickets[0].get('root_cause')}")
    if not root_cause_lines:
        root_cause_lines.append("当前已锁定目标告警，但仍缺少足够证据来给出明确根因。")

    return dedent(
        f"""
        # AIOps 默认巡检报告

        ## 巡检任务
        - {input_text}

        ## 目标告警
        - 服务: `{service_name}`
        - 告警: `{alert_name}`
        - 严重级别: `{severity}`
        - 实例: `{instance}`
        - 持续时间: `{duration}`

        ## 关键证据
        - CPU 平均值: {cpu_stats.get('avg', '该字段未返回')}%
        - CPU 峰值: {cpu_stats.get('max', '该字段未返回')}%
        - 是否检测到尖峰: {cpu_stats.get('spike_detected', '该字段未返回')}
        - 热点进程: {top_process.get('instance', '该字段未返回')} pid={top_process.get('pid', '该字段未返回')} CPU={top_process.get('cpu_percent', '该字段未返回')}% MEM={top_process.get('memory_percent', '该字段未返回')}%
        - 日志条数: {len(logs)}
        - 首条日志证据: {first_log.get('level', '该字段未返回')} {first_log.get('message', '该字段未返回') if first_log else '该字段未返回'}
        - 历史工单数量: {len(tickets)}
        - 服务依赖: {dependencies}

        ## 根因分析
        {chr(10).join(f"- {item}" for item in root_cause_lines)}

        ## 影响范围
        - 告警服务: `{service_name}`
        - 目标实例: `{instance}`
        - 依赖上下文: {dependencies}

        ## Runbook 参考
        - {str(knowledge)[:220] if knowledge else "该字段未返回"}

        ## 风险提示
        - 本次仅采集只读证据，未执行任何危险操作。
        - 若后续需要重启服务、扩容实例或执行清理动作，应走人工审批。

        ## 处理建议
        - 优先排查热点进程对应的重试风暴、积压或异常循环。
        - 对照历史工单和 runbook 校验是否需要限流、扩容或修复下游依赖。
        - 在补齐更多日志证据前，不要直接执行重启或缩容。
        """
    ).strip() + web_search_section


def build_patrol_verifier_findings(
    *,
    response: str,
    target_alert: dict[str, Any] | None,
    past_steps: list[tuple[str, str]],
    matched_skills: list[dict[str, Any]] | None,
) -> tuple[list[str], list[str], list[str], list[str]]:
    findings: list[str] = []
    suggested: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []

    if not target_alert:
        return findings, suggested, missing, warnings

    alert_name = target_alert.get("alert_name", "")
    service_name = target_alert.get("service_name", "")
    required = required_evidence_summary(target_alert, matched_skills)
    collected = parse_tool_results_from_history(past_steps)
    lowered = response.lower()
    web_search_used = _is_usable_evidence(collected.get("web_search"))

    if alert_name and alert_name not in response:
        findings.append("报告中缺少 target_alert。")
        missing.append("target_alert")
        suggested.append("在报告中明确写出告警名称和对应服务。")

    if service_name and service_name not in response:
        findings.append("报告中缺少 target_alert 对应的 service_name。")
        missing.append("service_name")
        suggested.append("在报告中明确写出告警服务名。")

    for evidence_type, tools in required.items():
        if not any(_is_usable_evidence(collected.get(tool_name)) for tool_name in tools):
            findings.append(f"缺少 {evidence_type} 证据。")
            missing.append(evidence_type)
            suggested.append(f"补充 {evidence_type} 对应的工具证据。")

    if "风险提示" not in response:
        findings.append("报告中缺少风险提示。")
        missing.append("风险提示")
        suggested.append("补充风险提示，并说明危险操作需要人工审批。")

    if "未执行任何危险操作" not in response and "未执行任何删除操作" not in response:
        findings.append("报告未明确说明没有执行危险操作。")
        warnings.append("需要显式声明未执行任何危险操作。")
        suggested.append("在报告中明确写出未执行任何危险操作。")

    if "unknown" in lowered:
        findings.append("报告包含 unknown 占位符，说明存在未处理的数据缺口。")
        suggested.append("将 unknown 替换为“该字段未返回”，并只基于真实证据得出结论。")

    if web_search_used:
        if "联网搜索补充资料" not in response:
            findings.append("使用了联网搜索，但报告中缺少“联网搜索补充资料”段落。")
            suggested.append("在报告中单独列出联网搜索资料的标题、链接、摘要和用途。")
        if "链接:" not in response or "标题:" not in response:
            findings.append("联网资料缺少标题或链接。")
            suggested.append("补充联网资料的标题和链接。")
        if any(not any(_is_usable_evidence(collected.get(tool_name)) for tool_name in tools) for tools in required.values()):
            findings.append("报告不能只依赖联网搜索推断本地系统根因。")
            suggested.append("先补齐本地监控、日志、工单或本地 runbook 证据，再使用联网资料做外部参考。")

    return findings, suggested, missing, warnings
