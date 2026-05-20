import asyncio
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLANNER_PATH = ROOT / "app" / "agent" / "aiops" / "planner.py"


def _load_module(module_name: str, path: Path):
    spec = spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class _FakePrompt:
    def __init__(self, result):
        self._result = result

    def __or__(self, _other):
        result = self._result

        class _FakeChain:
            async def ainvoke(self, _payload):
                return result

        return _FakeChain()


class _FakeChatPromptTemplate:
    @classmethod
    def from_messages(cls, _messages):
        return _FakePrompt({})


class _FakeLLM:
    def with_structured_output(self, _schema):
        return self


class _FakeLLMFactory:
    def create_qwen_chat_model(self, **_kwargs):
        return _FakeLLM()


def _install_stub(module_name: str, **attrs):
    module = types.ModuleType(module_name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[module_name] = module
    return module


sys.modules.setdefault("app", types.ModuleType("app"))
sys.modules["app"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.agent", types.ModuleType("app.agent"))
sys.modules["app.agent"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.agent.aiops", types.ModuleType("app.agent.aiops"))
sys.modules["app.agent.aiops"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.core", types.ModuleType("app.core"))
sys.modules["app.core"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("app.tools", types.ModuleType("app.tools"))
sys.modules["app.tools"].__path__ = []  # type: ignore[attr-defined]
sys.modules.setdefault("langchain_core", types.ModuleType("langchain_core"))
sys.modules["langchain_core"].__path__ = []  # type: ignore[attr-defined]

_install_stub("langchain_core.prompts", ChatPromptTemplate=_FakeChatPromptTemplate)
_install_stub(
    "app.agent.aiops.followup_context",
    build_followup_context_package=lambda current, previous: f"current={current}; previous={previous}",
)
_install_stub("app.agent.aiops.incident_memory", find_similar_incidents=lambda *_args, **_kwargs: [])
_install_stub(
    "app.agent.aiops.investigation",
    build_evidence_store=lambda *_args, **_kwargs: {},
    build_unsupported_profile_report=lambda **_kwargs: "unsupported",
    decide_stop_action=lambda **_kwargs: {},
    get_profile=lambda *_args, **_kwargs: None,
    get_runtime=lambda *_args, **_kwargs: None,
    supports_profile_execution=lambda *_args, **_kwargs: False,
)
_install_stub("app.agent.aiops.patrol", summarize_alerts=lambda *_args, **_kwargs: "")
_install_stub("app.agent.aiops.profile_loader", get_agent_profile_prompt=lambda: "")
_install_stub("app.agent.aiops.state", PlanExecuteState=dict)
_install_stub("app.agent.aiops.tool_registry", get_aiops_local_tools=lambda: [])
_install_stub(
    "app.agent.aiops.trace",
    create_trace_event=lambda **kwargs: kwargs,
    summarize_result=lambda payload: str(payload),
)
_install_stub(
    "app.agent.aiops.utils",
    format_tools_description=lambda *_args, **_kwargs: "",
    invoke_tool=lambda *_args, **_kwargs: None,
)
_install_stub("app.agent.mcp_client", get_mcp_client_with_retry=lambda: None)
_install_stub("app.config", config=types.SimpleNamespace(rag_model="test-rag-model"))
_install_stub("app.core.llm_factory", llm_factory=_FakeLLMFactory())
_install_stub("app.tools", retrieve_knowledge=object())


planner = _load_module("app.agent.aiops.planner", PLANNER_PATH)


def test_explanation_followup_helper_no_nameerror():
    planner.followup_answer_prompt = _FakePrompt({"response": "这是基于上一轮 CPU 诊断上下文的解释。"})
    planner.llm_factory = _FakeLLMFactory()

    result = asyncio.run(
        planner._answer_followup_from_previous_context(
            input_text="为什么你建议先观察热点进程？",
            previous_aiops_context={
                "previous_diagnosis_summary": "上一轮判断存在 CPU 压力。",
                "previous_recommendations": "建议先观察热点进程。",
                "previous_action_safety_notes": "未执行高风险操作。",
            },
            resolution_reason="The user is asking for an explanation.",
        )
    )

    assert "解释" in result


def test_failed_remediation_followup_resolution_no_nameerror():
    planner.followup_resolution_prompt = _FakePrompt(
        {
            "resolution": "use_tavily_external_search",
            "reason": "The previous local advice did not work.",
            "local_knowledge_query": "",
            "external_search_query": "cpu troubleshooting after remediation failed",
        }
    )
    planner.llm_factory = _FakeLLMFactory()

    result = asyncio.run(
        planner._resolve_followup_resolution(
            input_text="按你说的重新运行了没有效果",
            previous_aiops_context={
                "previous_profile_id": "cpu_pressure_profile",
                "previous_target_object": "demo-server-01",
                "previous_runbook_summary": "上一轮已使用本地 Runbook。",
                "previous_external_search_used": False,
            },
            remediation_feedback_failed=True,
        )
    )

    assert result["resolution"] == "use_tavily_external_search"
