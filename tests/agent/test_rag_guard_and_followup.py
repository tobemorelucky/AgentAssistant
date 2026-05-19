import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAG_GUARD_PATH = ROOT / "app" / "services" / "rag_answer_guard.py"
FOLLOWUP_CONTEXT_PATH = ROOT / "app" / "agent" / "aiops" / "followup_context.py"


def _load_module(module_name: str, path: Path):
    spec = spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


sys.modules.setdefault("app", types.ModuleType("app"))
sys.modules["app"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.services", types.ModuleType("app.services"))
sys.modules["app.services"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.agent", types.ModuleType("app.agent"))
sys.modules["app.agent"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.agent.aiops", types.ModuleType("app.agent.aiops"))
sys.modules["app.agent.aiops"].__path__ = []  # type: ignore[attr-defined]


rag_guard = _load_module("app.services.rag_answer_guard", RAG_GUARD_PATH)
followup_context = _load_module("app.agent.aiops.followup_context", FOLLOWUP_CONTEXT_PATH)


def test_rag_guard_blocks_realtime_disk_question():
    assert rag_guard.is_realtime_status_request_in_rag("请检查服务器当前磁盘空间使用情况，并分析主要占用来源。") is True


def test_rag_guard_blocks_realtime_cpu_question():
    assert rag_guard.is_realtime_status_request_in_rag("系统现在 CPU 状况如何？") is True


def test_rag_guard_blocks_realtime_health_question():
    assert rag_guard.is_realtime_status_request_in_rag("当前服务器是否异常？") is True


def test_rag_guard_answer_redirects_to_aiops_without_mock_facts():
    answer = rag_guard.build_rag_realtime_guard_answer("请检查服务器当前磁盘空间使用情况，并分析主要占用来源。")
    assert "AIOps 模式" in answer
    assert "历史案例/示例参考" in answer
    assert "demo-server-01" not in answer
    assert "92.4%" not in answer


def test_followup_relation_independent_for_fresh_cpu_question():
    relation = followup_context.classify_followup_relation("CPU 状况怎么样？", None)
    assert relation["relation_type"] == "independent"
    assert relation["recommended_handling"] == "new_diagnosis"


def test_followup_relation_dependent_when_previous_context_exists():
    relation = followup_context.classify_followup_relation(
        "为什么你建议先看 Docker build cache？",
        {"previous_user_query": "请检查服务器当前磁盘空间使用情况，并分析主要占用来源。"},
    )
    assert relation["relation_type"] == "dependent_followup"
    assert relation["recommended_handling"] == "followup_decision"


def test_followup_relation_ambiguous_without_previous_context():
    relation = followup_context.classify_followup_relation("按你说的做了，还是没效果。", None)
    assert relation["relation_type"] == "ambiguous"
    assert relation["recommended_handling"] == "followup_decision"


def test_remediation_feedback_failed_matches_retry_failure_variants():
    assert followup_context.is_remediation_feedback_failed("按你说的重新运行了没有效果") is True
    assert followup_context.is_remediation_feedback_failed("按你说的重新执行了还是不行") is True
    assert followup_context.is_remediation_feedback_failed("这个方案没用，继续查别的方法") is True
    assert followup_context.is_remediation_feedback_failed("做完之后没有改善，依旧异常") is True
    assert followup_context.is_remediation_feedback_failed("为什么你建议先观察热点进程？") is False


def test_previous_aiops_context_is_compacted_from_report_and_evidence():
    state = {
        "input": "请检查服务器当前磁盘空间使用情况，并分析主要占用来源。",
        "selected_profile": {"profile_id": "disk_pressure_profile"},
        "target_alert": {"alert_name": "HighDiskUsage", "host": "vm-01"},
        "response": (
            "# AIOps 磁盘专项诊断报告\n\n"
            "## 已确认事实\n"
            "- 主机：`vm-01`\n"
            "- 磁盘使用率：88.0%\n\n"
            "## 本地 Runbook / RAG 参考\n"
            "- 本地知识库建议先核对 Docker build cache。\n\n"
            "## 处理建议\n"
            "- 先看 Docker build cache。\n\n"
            "## 风险提示\n"
            "- 本轮未执行任何清理操作。\n"
        ),
        "evidence_store": {
            "disk_usage": {
                "status": "collected",
                "payload": {"usage_percent": 88.0, "host": "vm-01"},
            },
            "large_files": {
                "status": "collected",
                "payload": {"files": [{"path": "/swap.img"}]},
            },
        },
    }
    summary = followup_context.build_previous_aiops_context(state)
    assert summary["previous_profile_id"] == "disk_pressure_profile"
    assert summary["previous_target_object"] == "vm-01"
    assert "Docker build cache" in summary["previous_recommendations"]
    assert summary["previous_external_search_used"] is False
