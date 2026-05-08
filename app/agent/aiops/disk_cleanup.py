"""Deterministic disk-cleanup helpers for the AIOps workflow."""

from __future__ import annotations

import json
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


def is_disk_cleanup_skill(skill: dict[str, Any]) -> bool:
    return skill.get("name") == DISK_SKILL_NAME


def is_disk_cleanup_request(input_text: str, matched_skills: list[dict[str, Any]] | None = None) -> bool:
    normalized = (input_text or "").lower()
    if any(is_disk_cleanup_skill(skill) for skill in matched_skills or []):
        return True
    keywords = (
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
    return any(keyword in normalized for keyword in keywords)


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


def parse_disk_step_results(past_steps: list[tuple[str, str]]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for step, result in past_steps:
        tool_name = extract_disk_tool_name(step)
        if not tool_name:
            continue
        try:
            evidence[tool_name] = json.loads(result)
        except json.JSONDecodeError:
            evidence[tool_name] = result
    return evidence


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def build_disk_cleanup_report(input_text: str, past_steps: list[tuple[str, str]]) -> str:
    evidence = parse_disk_step_results(past_steps)
    disk_usage = _as_dict(evidence.get("get_disk_usage", {}))
    directories = _as_list(_as_dict(evidence.get("list_large_directories", {})).get("directories", []))
    files = _as_list(_as_dict(evidence.get("list_large_files", {})).get("files", []))
    deleted_open_files = _as_list(_as_dict(evidence.get("query_deleted_open_files", {})).get("files", []))
    docker_usage = _as_dict(evidence.get("query_docker_disk_usage", {}))
    cleanup_candidates = _as_dict(evidence.get("get_disk_cleanup_candidates", {}))
    knowledge = evidence.get("retrieve_knowledge", "")

    usage_percent = disk_usage.get("usage_percent", "unknown")
    used_gb = disk_usage.get("used_gb", "unknown")
    total_gb = disk_usage.get("total_gb", "unknown")
    available_gb = disk_usage.get("available_gb", "unknown")
    host = disk_usage.get("host", "demo-server-01")
    mount = disk_usage.get("mount", "/")
    top_log_dir = next((item for item in directories if item.get("path") == "/var/log"), directories[0] if directories else {})
    app_log = next((item for item in files if item.get("path") == "/var/log/data-sync-service/app.log"), files[0] if files else {})
    error_log = next((item for item in files if item.get("path") == "/var/log/data-sync-service/error.log"), {})
    deleted_file = deleted_open_files[0] if deleted_open_files else {}

    top_directories = "\n".join(
        f"- `{item.get('path')}`: {item.get('size_gb')}GB"
        for item in directories[:4]
    ) or "- 未采集到目录占用数据"
    top_files = "\n".join(
        f"- `{item.get('path')}`: {item.get('size_gb')}GB"
        for item in files[:5]
    ) or "- 未采集到大文件数据"
    deleted_files_text = "\n".join(
        (
            f"- 进程 `{item.get('process_name')}` (pid={item.get('pid')}) 持有 `{item.get('path')}`，"
            f"状态为 `{item.get('state')}`，占用 {item.get('size_gb')}GB"
        )
        for item in deleted_open_files
    ) or "- 未发现 deleted open files"

    docker_total = docker_usage.get("total_gb", "unknown")
    docker_text = dedent(
        f"""
        - Docker 总占用：{docker_total}GB
        - images：{docker_usage.get('images_gb', 'unknown')}GB
        - containers：{docker_usage.get('containers_gb', 'unknown')}GB
        - volumes：{docker_usage.get('volumes_gb', 'unknown')}GB
        - build cache：{docker_usage.get('build_cache_gb', 'unknown')}GB
        """
    ).strip()

    safe_text = "\n".join(
        f"- {item.get('item')}：{item.get('size_gb')}GB，建议 `{item.get('suggestion')}`"
        for item in (cleanup_candidates.get("safe") or [])
    ) or "- 无"
    approval_text = "\n".join(
        f"- {item.get('item')}：{item.get('size_gb')}GB，需人工确认，建议 `{item.get('suggestion')}`"
        for item in (cleanup_candidates.get("need_approval") or [])
    ) or "- 无"
    forbidden_text = "\n".join(
        f"- {item.get('item')}：原因 `{item.get('reason')}`"
        for item in (cleanup_candidates.get("forbidden") or [])
    ) or "- 无"

    return dedent(
        f"""
        # AIOps 磁盘清理诊断报告

        ## 用户任务
        - {input_text}

        ## 磁盘使用率证据
        - 主机 `{host}` 的挂载点 `{mount}` 当前使用率为 **{usage_percent}%**。
        - 已使用 **{used_gb}GB / {total_gb}GB**，剩余可用空间仅 **{available_gb}GB**。
        - 这已经属于高水位状态，若继续增长，容易触发 `No space left on device`、日志写入失败、容器调度失败或业务落盘异常。

        ## Top 目录占用
        {top_directories}

        ## Top 大文件
        {top_files}

        ## Docker 占用情况
        {docker_text}

        ## Deleted Open Files
        {deleted_files_text}

        ## 根因分析
        - 当前根挂载点达到 **{usage_percent}%**，主要压力来自 `/var/log`、`/var/lib/docker`、`/tmp` 与 `/app/cache`。
        - 其中 `/var/log/data-sync-service/app.log` **{app_log.get('size_gb', 'unknown')}GB**、`error.log` **{error_log.get('size_gb', 'unknown')}GB**，说明业务日志滚动与归档策略不足。
        - `data-sync-service` 仍持有一个已删除的 `old.log` 文件句柄，占用 **{deleted_file.get('size_gb', 'unknown')}GB**，即使文件名已删除，空间仍未归还。
        - Docker 总占用 **{docker_total}GB**，其中 build cache 占 **{docker_usage.get('build_cache_gb', 'unknown')}GB**，说明镜像/构建缓存长期未清理。

        ## 关键证据
        - 根分区使用率 **{usage_percent}%**
        - `/var/log` 占用 **{top_log_dir.get('size_gb', 'unknown')}GB**
        - `/var/log/data-sync-service/app.log` 占用 **{app_log.get('size_gb', 'unknown')}GB**
        - deleted open file 占用 **{deleted_file.get('size_gb', 'unknown')}GB**
        - Docker 总占用 **{docker_total}GB**

        ## 影响范围
        - 受影响主机：`{host}`
        - 重点业务：`data-sync-service`
        - 直接风险：日志继续增长会放大根分区压力，构建缓存与临时目录持续膨胀会压缩业务可用空间。

        ## 可安全清理项
        {safe_text}

        ## 需人工确认项
        {approval_text}

        ## 禁止自动清理项
        {forbidden_text}

        ## 风险提示
        - 本次诊断 **没有执行任何删除操作**，以下内容仅作为人工确认后的建议。
        - `rm -rf`、`docker system prune --volumes`、删除数据库目录、删除业务 `uploads/`、删除 Milvus/MinIO/etcd volumes 都必须视为高风险或禁止自动执行。
        - 对 deleted open files，只有在确认业务可重启或安全滚动的前提下，才能通过重启进程释放空间。

        ## 处理建议
        - 先清理安全项，优先回收临时目录、已归档日志和 Docker build cache。
        - 对 `/var/log/data-sync-service/app.log` 和 `error.log`，先确认日志保留策略，再进行轮转、压缩或归档。
        - 对 `data-sync-service` 持有的 deleted open file，建议在业务低峰执行平滑重启，释放 **6.8GB** 隐性占用。
        - 对 Docker 卷和镜像清理，必须在确认未被在线业务依赖后再执行。

        ## 后续预防措施
        - 为 `data-sync-service` 配置日志轮转与保留上限，避免单个日志文件持续增长到 {app_log.get('size_gb', 'unknown')}GB。
        - 为 `/var/lib/docker` 定期审计镜像、卷与 build cache，建立月度清理窗口。
        - 为根分区设置 80% / 90% 双阈值告警，并将 `HighDiskUsage` 与 `DiskFull` 直接关联到磁盘清理 runbook。
        - 将 deleted open files 检查加入巡检项，避免“文件已删但空间未释放”的隐性泄漏长期积累。

        ## Runbook 参考
        - aiops-docs 中上传的 runbook 仅作为知识库参考，不代表实时日志或实时监控数据。
        - 检索关键词：`{DISK_KNOWLEDGE_QUERY}`
        - 本次参考摘要：{str(knowledge)[:280] if knowledge else "未命中额外 runbook 内容"}
        """
    ).strip()
