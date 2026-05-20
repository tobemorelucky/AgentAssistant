import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UTILS_PATH = ROOT / "app" / "agent" / "aiops" / "utils.py"
FOLLOWUP_REPORT_PATH = ROOT / "app" / "agent" / "aiops" / "followup_report.py"


def _load_module(module_name: str, path: Path):
    spec = spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


sys.modules.setdefault("app", types.ModuleType("app"))
sys.modules["app"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.agent", types.ModuleType("app.agent"))
sys.modules["app.agent"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.agent.aiops", types.ModuleType("app.agent.aiops"))
sys.modules["app.agent.aiops"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("langchain_core", types.ModuleType("langchain_core"))
sys.modules["langchain_core"].__path__ = []  # type: ignore[attr-defined]
documents_module = types.ModuleType("langchain_core.documents")


class _Document:
    def __init__(self, page_content: str, metadata: dict | None = None):
        self.page_content = page_content
        self.metadata = metadata or {}


documents_module.Document = _Document  # type: ignore[attr-defined]
sys.modules["langchain_core.documents"] = documents_module


utils = _load_module("app.agent.aiops.utils", UTILS_PATH)
followup_report = _load_module("app.agent.aiops.followup_report", FOLLOWUP_REPORT_PATH)


def test_normalize_external_reference_result_accepts_string():
    payload = utils.normalize_external_reference_result("外部资料建议先检查 uvicorn worker 数量与 CPU 争用。")
    assert payload["ok"] is True
    assert "uvicorn" in payload["content"]
    assert payload["source"] == "external_reference"


def test_normalize_external_reference_result_accepts_results_list():
    payload = utils.normalize_external_reference_result(
        {
            "results": [
                {
                    "title": "Linux high CPU troubleshooting",
                    "url": "https://example.com/cpu",
                    "content": "Check top CPU processes and runaway workers.",
                }
            ]
        }
    )
    assert payload["ok"] is True
    assert payload["artifacts"]
    assert payload["artifacts"][0]["metadata"]["title"] == "Linux high CPU troubleshooting"


def test_normalize_external_reference_result_accepts_plain_list():
    payload = utils.normalize_external_reference_result(
        [
            {
                "title": "Docker cache cleanup",
                "url": "https://example.com/docker",
                "content": "Review docker build cache before pruning.",
            }
        ]
    )
    assert payload["ok"] is True
    assert "Docker cache cleanup" in payload["content"]


def test_followup_external_report_uses_compact_summary_and_external_reason():
    report = followup_report.build_followup_enrichment_report(
        {
            "input": "按你说的修改了还是没用",
            "followup_resolution": {
                "resolution": "use_tavily_external_search",
                "reason": "旧的兜底原因文本",
            },
            "previous_aiops_context": {
                "previous_user_query": "CPU满了怎么办",
                "previous_profile_id": "cpu_pressure_profile",
                "previous_target_object": "demo-server-01",
                "previous_diagnosis_summary": "报告原文开头不应该被原样拷贝。",
                "previous_key_evidence": [
                    "cpu_summary: usage=88.7%",
                    "top_cpu_processes: top=python-worker",
                    "top_cpu_processes: top=java-gateway",
                ],
                "previous_recommendations": "先观察热点进程，再检查 worker 数量与限流策略。",
                "previous_runbook_summary": "本地 Runbook 已给出基础 CPU 排查建议。",
                "previous_action_safety_notes": "本轮未执行任何重启或 kill -9 操作。",
            },
            "evidence_store": {
                "external_reference": {
                    "payload": {
                        "ok": True,
                        "content": "外部资料建议区分 CPU 百分比高与 load high，并检查 io wait 与线程级热点。",
                        "artifacts": [
                            {
                                "page_content": "Check uvicorn worker count before restarting services. Review load average and io wait. Inspect hot threads if process-level hotspots are insufficient.",
                                "metadata": {
                                    "title": "Uvicorn CPU troubleshooting",
                                    "source": "https://example.com/uvicorn-cpu",
                                },
                            }
                        ],
                    }
                }
            },
        }
    )
    assert "AIOps 追问补充诊断报告" in report
    assert "demo-server-01" in report
    assert "上一轮判断 `demo-server-01` 存在CPU 压力" in report
    assert "上一轮已基于本地 Runbook 给出处理建议，但用户反馈仍未解决，因此补充外部公开资料寻找新的排查思路。" in report
    assert "外部补充参考：区分“CPU 百分比高”和“load 高但伴随 I/O wait”" in report
    assert "外部补充参考：若进程级热点不足以解释问题，可进一步下钻到线程级热点" in report
    assert "unknown-host" not in report
    assert "未成功获取实时 CPU 摘要" not in report


def test_followup_local_report_does_not_claim_realtime_failure():
    report = followup_report.build_followup_enrichment_report(
        {
            "input": "还有别的本地处理思路吗",
            "followup_resolution": {
                "resolution": "retrieve_more_local_knowledge",
                "reason": "追加本地 Runbook 以补充处置思路。",
            },
            "previous_aiops_context": {
                "previous_user_query": "CPU满了怎么办",
                "previous_profile_id": "cpu_pressure_profile",
                "previous_target_object": "demo-server-01",
                "previous_diagnosis_summary": "CPU 压力集中在 python-worker。",
                "previous_key_evidence": ["cpu_summary: usage=88.7%"],
                "previous_recommendations": "先观察热点进程。",
            },
            "evidence_store": {
                "cpu_runbook": {
                    "payload": {
                        "ok": True,
                        "content": "补充建议：检查 worker 数量、线程池与限流配置。",
                    }
                }
            },
        }
    )
    assert "AIOps 追问补充诊断报告" in report
    assert "补充建议：检查 worker 数量、线程池与限流配置。" in report
    assert "未成功获取实时 CPU 摘要" not in report
