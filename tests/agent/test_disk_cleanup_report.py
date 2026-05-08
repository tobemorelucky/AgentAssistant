from app.agent.aiops.disk_cleanup import (
    build_disk_cleanup_report,
    build_disk_verifier_findings,
    normalize_disk_tool_result,
    summarize_disk_tool_result,
)


def test_normalize_disk_usage_from_mcp_text_block():
    raw_result = [
        {
            "type": "text",
            "text": '{"host":"demo-server-01","mount":"/","usage_percent":92.4,"used_gb":184.8,"total_gb":200,"available_gb":15.2}',
            "id": "lc_1",
        }
    ]

    normalized = normalize_disk_tool_result("get_disk_usage", raw_result)

    assert normalized["host"] == "demo-server-01"
    assert normalized["usage_percent"] == 92.4
    assert normalized["status"] == "critical"


def _sample_past_steps():
    return [
        (
            "调用 get_disk_usage 获取 demo-server-01 主机根挂载点 / 的磁盘使用率证据。",
            '{"host":"demo-server-01","mount":"/","usage_percent":92.4,"used_gb":184.8,"total_gb":200,"available_gb":15.2,"status":"critical"}',
        ),
        (
            "调用 list_large_directories 获取 / 下的高占用目录排行，定位 Top 目录占用。",
            '{"path":"/","limit":10,"directories":[{"path":"/var/log","size_gb":48.2,"reason":"业务日志与归档日志堆积"},{"path":"/var/lib/docker","size_gb":37.5,"reason":"Docker 镜像、卷或构建缓存占用"},{"path":"/tmp","size_gb":12.1,"reason":"临时文件未定期清理"}]}',
        ),
        (
            "调用 list_large_files 获取 / 下的大文件清单，定位最占空间的日志和缓存文件。",
            '{"path":"/","min_size_mb":100,"limit":20,"files":[{"path":"/var/log/data-sync-service/app.log","size_gb":18.6,"safe_action":"先确认日志保留策略，再执行轮转、压缩或归档","risk":"直接删除可能影响审计、排障或业务写入"},{"path":"/var/lib/docker/buildkit/cache.db","size_gb":9.4,"safe_action":"需要结合业务影响评估后再处理","risk":"需要确认文件是否被在线业务依赖"},{"path":"/var/log/data-sync-service/error.log","size_gb":7.4,"safe_action":"先确认日志保留策略，再执行轮转、压缩或归档","risk":"直接删除可能影响审计、排障或业务写入"}]}',
        ),
        (
            "调用 query_deleted_open_files 检查是否存在已删除但仍被进程持有的文件句柄。",
            '{"files":[{"process":"data-sync-service","pid":12345,"file":"/var/log/data-sync-service/old.log","state":"deleted","size_gb":6.8,"suggestion":"在业务低峰平滑重启 data-sync-service，释放已删除但未归还的磁盘空间"}],"total":1}',
        ),
        (
            "调用 query_docker_disk_usage 采集 Docker 镜像、容器、卷和构建缓存占用。",
            '{"images_gb":4.2,"containers_gb":1.6,"volumes_gb":6.1,"build_cache_gb":9.4,"total_gb":21.3}',
        ),
        (
            "调用 get_disk_cleanup_candidates 汇总可安全清理项、需人工确认项和禁止自动清理项。",
            '{"safe":[{"item":"清理 /tmp 历史临时文件","size_gb":12.1,"suggestion":"清理 7 天前的临时文件"}],"need_approval":[{"item":"轮转 /var/log/data-sync-service/app.log","size_gb":18.6,"suggestion":"确认日志保留策略后执行 logrotate"}],"forbidden":[{"item":"docker system prune --volumes","reason":"可能误删在线业务依赖的数据卷"}]}',
        ),
    ]


def test_disk_cleanup_report_contains_concrete_evidence():
    report = build_disk_cleanup_report("服务器磁盘使用率过高，怀疑硬盘满了，请给出清理建议", _sample_past_steps())

    assert "92.4%" in report
    assert "48.2GB" in report
    assert "18.6GB" in report
    assert "21.3GB" in report
    assert "没有执行任何删除操作" in report
    assert "unknown" not in report.lower()


def test_disk_cleanup_verifier_fails_unknown_report():
    bad_report = """
    # AIOps 磁盘清理诊断报告
    - 磁盘使用率为 unknown%
    - 未采集到目录占用数据
    - 根因分析：/var/log 是主要压力来源
    - 未采集到大文件数据
    - 根因分析：/var/log/data-sync-service/app.log 是主要大文件
    - Docker 占用 unknownGB
    - 可安全清理项：无
    - 需人工确认项：无
    - 禁止自动清理项：无
    """

    findings, suggested, missing, warnings = build_disk_verifier_findings(bad_report, _sample_past_steps())

    assert findings
    assert any("unknown" in finding.lower() for finding in findings)
    assert suggested
    assert missing or warnings


def test_disk_cleanup_summary_contains_key_fields():
    summary = summarize_disk_tool_result(
        "query_docker_disk_usage",
        {"images_gb": 4.2, "containers_gb": 1.6, "volumes_gb": 6.1, "build_cache_gb": 9.4, "total_gb": 21.3},
    )

    assert "images=4.2GB" in summary
    assert "build_cache=9.4GB" in summary
