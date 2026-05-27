"""Registry of supported remediation actions."""

from __future__ import annotations

from app.agent.aiops.remediation.action_schema import RemediationActionDefinition


_ACTION_DEFINITIONS = [
    RemediationActionDefinition(
        action_id="cleanup_tmp_old_files",
        title="清理临时目录中过期文件",
        description="评估并清理 /tmp 等临时目录中的历史遗留文件。",
        profile_ids=["disk_pressure_profile"],
        risk_level="safe_dry_run",
        dry_run_supported=True,
        approval_required=True,
        expected_benefit="释放临时文件占用的磁盘空间。",
        safety_note="仅应在确认文件可回收且不影响业务后执行。",
    ),
    RemediationActionDefinition(
        action_id="vacuum_journal_logs",
        title="压缩/清理 journal 日志",
        description="评估 systemd journal 日志体积并执行保守清理。",
        profile_ids=["disk_pressure_profile"],
        risk_level="safe_dry_run",
        dry_run_supported=True,
        approval_required=True,
        expected_benefit="降低日志对根分区空间的持续占用。",
        safety_note="需确认日志保留策略与审计要求。",
    ),
    RemediationActionDefinition(
        action_id="docker_builder_prune",
        title="清理 Docker build cache",
        description="评估 Docker builder cache、无用层与构建缓存占用。",
        profile_ids=["disk_pressure_profile"],
        risk_level="approval_required",
        dry_run_supported=True,
        approval_required=True,
        expected_benefit="释放 Docker build cache 占用的磁盘空间。",
        safety_note="必须确认缓存不影响当前部署与构建链路。",
    ),
    RemediationActionDefinition(
        action_id="cleanup_app_cache",
        title="清理应用缓存目录",
        description="评估应用缓存目录占用并执行保守清理。",
        profile_ids=["disk_pressure_profile"],
        risk_level="approval_required",
        dry_run_supported=True,
        approval_required=True,
        expected_benefit="回收应用缓存膨胀导致的磁盘占用。",
        safety_note="需确认缓存目录可重建且不会影响线上功能。",
    ),
    RemediationActionDefinition(
        action_id="observe_top_process",
        title="持续观察热点 CPU 进程",
        description="继续观察热点 CPU 进程与负载趋势。",
        profile_ids=["cpu_pressure_profile"],
        risk_level="read_only",
        dry_run_supported=False,
        approval_required=False,
        expected_benefit="帮助确认 CPU 压力是否持续存在。",
        safety_note="仅为观察建议，不执行任何变更。",
    ),
    RemediationActionDefinition(
        action_id="inspect_thread_or_worker",
        title="检查线程/Worker 热点",
        description="针对高 CPU 进程进一步检查线程、worker 或队列压力。",
        profile_ids=["cpu_pressure_profile"],
        risk_level="safe_dry_run",
        dry_run_supported=True,
        approval_required=False,
        expected_benefit="帮助定位 CPU 热点是否集中在特定线程或 worker。",
        safety_note="第一版仅作排查建议，不自动修改运行参数。",
    ),
    RemediationActionDefinition(
        action_id="reduce_concurrency",
        title="调整并发或限流",
        description="评估降低 worker 并发、限流或削峰的影响。",
        profile_ids=["cpu_pressure_profile"],
        risk_level="approval_required",
        dry_run_supported=True,
        approval_required=True,
        expected_benefit="缓解 CPU 饱和与请求排队问题。",
        safety_note="需要业务侧确认吞吐与延迟影响。",
    ),
    RemediationActionDefinition(
        action_id="restart_service",
        title="重启相关服务",
        description="评估并执行服务重启。",
        profile_ids=["cpu_pressure_profile", "memory_pressure_profile"],
        risk_level="approval_required",
        dry_run_supported=True,
        approval_required=True,
        expected_benefit="缓解异常进程状态、内存泄漏或僵死 worker。",
        safety_note="必须经过人工审批，避免业务中断。",
    ),
    RemediationActionDefinition(
        action_id="inspect_top_memory_process",
        title="检查热点内存进程",
        description="继续观察热点内存进程、RSS 增长与对象堆积。",
        profile_ids=["memory_pressure_profile"],
        risk_level="read_only",
        dry_run_supported=False,
        approval_required=False,
        expected_benefit="帮助确认主要内存压力来源。",
        safety_note="仅为观察建议，不执行任何变更。",
    ),
    RemediationActionDefinition(
        action_id="inspect_cache_growth",
        title="检查缓存增长",
        description="评估缓存目录、应用缓存或对象池增长情况。",
        profile_ids=["memory_pressure_profile"],
        risk_level="safe_dry_run",
        dry_run_supported=True,
        approval_required=False,
        expected_benefit="帮助区分缓存增长与真实泄漏。",
        safety_note="第一版仅作排查建议，不自动清理缓存。",
    ),
    RemediationActionDefinition(
        action_id="clear_app_cache",
        title="清理应用缓存",
        description="评估并清理可安全回收的应用缓存。",
        profile_ids=["memory_pressure_profile"],
        risk_level="approval_required",
        dry_run_supported=True,
        approval_required=True,
        expected_benefit="回收缓存膨胀导致的内存或磁盘占用。",
        safety_note="需确认缓存具备重建能力且不会影响线上流量。",
    ),
    RemediationActionDefinition(
        action_id="reboot_server",
        title="重启主机",
        description="主机级重启。",
        profile_ids=[],
        risk_level="forbidden",
        dry_run_supported=False,
        approval_required=True,
        expected_benefit="高风险动作，不作为自动化建议。",
        safety_note="主机重启永远禁止自动执行。",
    ),
    RemediationActionDefinition(
        action_id="delete_database_directory",
        title="删除数据库目录",
        description="删除数据库数据目录。",
        profile_ids=[],
        risk_level="forbidden",
        dry_run_supported=False,
        approval_required=True,
        expected_benefit="高风险动作，不作为自动化建议。",
        safety_note="数据库目录永远禁止自动执行。",
    ),
    RemediationActionDefinition(
        action_id="docker_system_prune_volumes",
        title="Docker system prune --volumes",
        description="删除 Docker 未使用资源及 volumes。",
        profile_ids=[],
        risk_level="forbidden",
        dry_run_supported=False,
        approval_required=True,
        expected_benefit="高风险动作，不作为自动化建议。",
        safety_note="删除 volumes 风险极高，永远禁止自动执行。",
    ),
]

ACTION_REGISTRY = {action.action_id: action for action in _ACTION_DEFINITIONS}


def get_action_definition(action_id: str) -> RemediationActionDefinition | None:
    return ACTION_REGISTRY.get(action_id)


def list_action_definitions() -> list[RemediationActionDefinition]:
    return list(ACTION_REGISTRY.values())


def list_profile_actions(profile_id: str) -> list[RemediationActionDefinition]:
    return [action for action in _ACTION_DEFINITIONS if profile_id in action.profile_ids]
