"""Run simple AIOps eval cases against the in-process service."""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
RESULTS_PATH = ROOT_DIR / "eval" / "results.json"
REPORT_PATH = ROOT_DIR / "eval" / "report.md"
CASES_PATH = ROOT_DIR / "eval" / "aiops_cases.jsonl"


def load_cases() -> list[dict[str, Any]]:
    cases = []
    for line in CASES_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


async def run_case(case: dict[str, Any]) -> dict[str, Any]:
    from app.services.aiops_service import aiops_service

    start = time.perf_counter()
    events = []
    async for event in aiops_service.execute(case["task"], session_id=f"eval-{case['id']}"):
        events.append(event)
    latency = round((time.perf_counter() - start) * 1000, 2)

    trace_events = [event for event in events if event.get("type") == "trace"]
    tool_names = [
        event.get("trace", {}).get("tool_name")
        for event in trace_events
        if event.get("trace", {}).get("tool_name")
    ]
    report_text = "\n".join(
        event.get("report", "") or event.get("response", "") or ""
        for event in events
        if event.get("type") in {"report", "complete"}
    )
    approval_triggered = any(event.get("type") == "approval_required" for event in events)
    verifier_passed = any(
        event.get("type") == "verifier_result" and event.get("passed")
        for event in events
    )

    expected_tools = case.get("expected_tools", [])
    tool_hit_rate = (
        len(set(tool_names) & set(expected_tools)) / len(expected_tools) if expected_tools else 1.0
    )
    evidence_coverage = 1.0 if "证据" in report_text else 0.0
    root_cause_match = 1.0 if "根因" in report_text else 0.0
    approval_accuracy = 1.0 if approval_triggered == case.get("expected_approval", False) else 0.0

    return {
        "id": case["id"],
        "latency_ms": latency,
        "tool_names": tool_names,
        "tool_hit_rate": tool_hit_rate,
        "evidence_coverage": evidence_coverage,
        "root_cause_match": root_cause_match,
        "approval_trigger_accuracy": approval_accuracy,
        "verifier_passed": verifier_passed,
    }


async def main() -> None:
    cases = load_cases()
    results = [await run_case(case) for case in cases]

    aggregate = {
        "tool_hit_rate": round(statistics.mean(item["tool_hit_rate"] for item in results), 4),
        "evidence_coverage": round(statistics.mean(item["evidence_coverage"] for item in results), 4),
        "root_cause_match": round(statistics.mean(item["root_cause_match"] for item in results), 4),
        "approval_trigger_accuracy": round(
            statistics.mean(item["approval_trigger_accuracy"] for item in results), 4
        ),
        "verifier_pass_rate": round(
            statistics.mean(1.0 if item["verifier_passed"] else 0.0 for item in results), 4
        ),
        "avg_latency": round(statistics.mean(item["latency_ms"] for item in results), 2),
        "cases": results,
    }

    RESULTS_PATH.write_text(json.dumps(aggregate, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(
        "\n".join(
            [
                "# AIOps Eval Report",
                "",
                f"- tool_hit_rate: {aggregate['tool_hit_rate']}",
                f"- evidence_coverage: {aggregate['evidence_coverage']}",
                f"- root_cause_match: {aggregate['root_cause_match']}",
                f"- approval_trigger_accuracy: {aggregate['approval_trigger_accuracy']}",
                f"- verifier_pass_rate: {aggregate['verifier_pass_rate']}",
                f"- avg_latency: {aggregate['avg_latency']} ms",
            ]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    asyncio.run(main())
