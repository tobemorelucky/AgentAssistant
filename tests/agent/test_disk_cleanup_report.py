from app.agent.aiops.disk_cleanup import build_disk_cleanup_report


def test_disk_cleanup_report_contains_key_mock_numbers():
    past_steps = [
        (
            "调用 get_disk_usage 获取 demo-server-01 主机根挂载点 / 的磁盘使用率证据。",
            '{"host":"demo-server-01","mount":"/","usage_percent":92.4,"used_gb":184.8,"total_gb":200,"available_gb":15.2}',
        ),
        (
            "调用 list_large_directories 获取 / 下的高占用目录排行，定位 Top 目录占用。",
            '{"directories":[{"path":"/var/log","size_gb":48.2},{"path":"/var/lib/docker","size_gb":37.5},{"path":"/tmp","size_gb":12.1},{"path":"/app/cache","size_gb":8.6}]}',
        ),
        (
            "调用 list_large_files 获取 / 下的大文件清单，定位最占空间的日志和缓存文件。",
            '{"files":[{"path":"/var/log/data-sync-service/app.log","size_gb":18.6},{"path":"/var/lib/docker/buildkit/cache.db","size_gb":9.4},{"path":"/var/log/data-sync-service/error.log","size_gb":7.4}]}',
        ),
        (
            "调用 query_deleted_open_files 检查是否存在已删除但仍被进程持有的文件句柄。",
            '{"files":[{"process_name":"data-sync-service","pid":12345,"path":"/var/log/data-sync-service/old.log","state":"deleted","size_gb":6.8}]}',
        ),
        (
            "调用 query_docker_disk_usage 采集 Docker 镜像、容器、卷和构建缓存占用。",
            '{"images_gb":4.2,"containers_gb":1.6,"volumes_gb":6.1,"build_cache_gb":9.4,"total_gb":21.3}',
        ),
        (
            "调用 get_disk_cleanup_candidates 汇总可安全清理项、需人工确认项和禁止自动清理项。",
            '{"safe":[{"item":"清理 /tmp 历史临时文件","size_gb":12.1,"suggestion":"清理超过 7 天未访问的临时文件"}],"need_approval":[{"item":"压缩或归档 /var/log/data-sync-service/app.log","size_gb":18.6,"suggestion":"确认日志保留策略后执行 logrotate 或归档"}],"forbidden":[{"item":"docker system prune --volumes","reason":"会删除卷数据，必须人工评估"}]}',
        ),
    ]

    report = build_disk_cleanup_report("服务器磁盘使用率过高，怀疑硬盘满了，请给出清理建议", past_steps)

    assert "92.4%" in report
    assert "48.2GB" in report
    assert "18.6GB" in report
    assert "21.3GB" in report
    assert "Deleted Open Files" in report
    assert "禁止自动清理项" in report
