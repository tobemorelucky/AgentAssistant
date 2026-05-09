from app.agent.aiops.patrol import (
    build_fallback_tool_plan,
    build_patrol_verifier_findings,
    choose_highest_severity_alert,
)


def test_choose_highest_severity_alert_prefers_critical():
    alerts = [
        {"service_name": "svc-a", "alert_name": "Warn", "severity": "warning"},
        {"service_name": "svc-b", "alert_name": "HighCPUUsage", "severity": "critical"},
    ]
    selected = choose_highest_severity_alert(alerts)
    assert selected is not None
    assert selected["service_name"] == "svc-b"


def test_high_cpu_fallback_plan_covers_required_evidence():
    steps = build_fallback_tool_plan(
        {"service_name": "data-sync-service", "alert_name": "HighCPUUsage", "severity": "critical"}
    )
    tool_names = [step.tool for step in steps]
    assert "query_cpu_metrics" in tool_names
    assert "query_process_list" in tool_names
    assert "search_log" in tool_names
    assert "search_historical_tickets" in tool_names
    assert "retrieve_knowledge" in tool_names


def test_patrol_verifier_rejects_unknown_and_missing_risk_notice():
    findings, suggested, missing, warnings = build_patrol_verifier_findings(
        response="报告里有 unknown，但没有风险提示。",
        target_alert={"service_name": "data-sync-service", "alert_name": "HighCPUUsage"},
        past_steps=[],
        matched_skills=[],
    )
    assert findings
    assert suggested
    assert "target_alert" not in missing
    assert warnings
