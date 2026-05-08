from app.agent.aiops.planner import (
    build_disk_cleanup_plan,
    build_default_alert_plan,
    choose_highest_severity_alert,
    should_fetch_active_alerts,
)


def test_default_mode_always_fetches_active_alerts():
    assert should_fetch_active_alerts("default", "") is True


def test_custom_current_alert_check_also_fetches_active_alerts():
    assert should_fetch_active_alerts("custom", "请检查当前系统是否存在活跃告警") is True


def test_highest_severity_alert_is_selected_first():
    alerts = [
        {"alert_name": "DiskWarning", "severity": "warning", "service_name": "storage"},
        {"alert_name": "HighCPUUsage", "severity": "critical", "service_name": "data-sync-service"},
        {"alert_name": "HighMemory", "severity": "high", "service_name": "api-gateway-service"},
    ]

    selected = choose_highest_severity_alert(alerts)

    assert selected is not None
    assert selected["alert_name"] == "HighCPUUsage"
    assert selected["service_name"] == "data-sync-service"


def test_default_plan_anchors_on_selected_alert():
    plan = build_default_alert_plan(
        {
            "alert_name": "HighCPUUsage",
            "severity": "critical",
            "service_name": "data-sync-service",
            "instance": "data-sync-service-01",
            "duration": "12m",
        }
    )

    assert len(plan) >= 5
    assert "get_service_info" in plan[0]
    assert "data-sync-service" in "\n".join(plan)
    assert "HighCPUUsage" in "\n".join(plan)


def test_disk_cleanup_plan_prioritizes_disk_tools():
    plan = build_disk_cleanup_plan()

    assert len(plan) == 7
    assert "get_disk_usage" in plan[0]
    assert "list_large_directories" in plan[1]
    assert "list_large_files" in plan[2]
    assert "query_deleted_open_files" in plan[3]
    assert "query_docker_disk_usage" in plan[4]
    assert "get_disk_cleanup_candidates" in plan[5]
    assert "retrieve_knowledge" in plan[6]
