import asyncio
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLANNER_PATH = ROOT / "app" / "agent" / "aiops" / "planner.py"


class _DummyPrompt:
    @classmethod
    def from_messages(cls, _messages):
        return cls()

    def __or__(self, other):
        return other


class _DummyLogger:
    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None


class _DummyRuntime:
    def build_initial_tasks(self, _state):
        return [
            {"slot": "memory_summary", "tool": "get_memory_summary", "args": {}, "required": True, "reason": "", "evidence_type": "memory_summary"},
            {"slot": "top_memory_processes", "tool": "list_top_memory_processes", "args": {"limit": 10}, "required": True, "reason": "", "evidence_type": "top_memory_processes"},
        ]


class _DummyMcpClient:
    async def get_tools(self):
        return []


def _load_planner_module():
    backups = {name: sys.modules.get(name) for name in [
        "langchain_core.prompts",
        "loguru",
        "app",
        "app.agent",
        "app.agent.aiops",
        "app.agent.aiops.followup_context",
        "app.agent.aiops.incident_memory",
        "app.agent.aiops.investigation",
        "app.agent.aiops.patrol",
        "app.agent.aiops.profile_loader",
        "app.agent.aiops.state",
        "app.agent.aiops.tool_registry",
        "app.agent.aiops.trace",
        "app.agent.aiops.utils",
        "app.agent.mcp_client",
        "app.config",
        "app.core.llm_factory",
        "app.tools",
    ]}

    langchain_prompts = types.ModuleType("langchain_core.prompts")
    langchain_prompts.ChatPromptTemplate = _DummyPrompt
    sys.modules["langchain_core.prompts"] = langchain_prompts

    loguru_module = types.ModuleType("loguru")
    loguru_module.logger = _DummyLogger()
    sys.modules["loguru"] = loguru_module

    fake_app = types.ModuleType("app")
    fake_app.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app"] = fake_app
    fake_agent = types.ModuleType("app.agent")
    fake_agent.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.agent"] = fake_agent
    fake_aiops = types.ModuleType("app.agent.aiops")
    fake_aiops.__path__ = []  # type: ignore[attr-defined]
    sys.modules["app.agent.aiops"] = fake_aiops

    followup_module = types.ModuleType("app.agent.aiops.followup_context")
    followup_module.build_followup_context_package = lambda *_args, **_kwargs: "followup"
    sys.modules["app.agent.aiops.followup_context"] = followup_module

    incident_module = types.ModuleType("app.agent.aiops.incident_memory")
    incident_module.find_similar_incidents = lambda *_args, **_kwargs: []
    sys.modules["app.agent.aiops.incident_memory"] = incident_module

    investigation_module = types.ModuleType("app.agent.aiops.investigation")
    investigation_module.build_evidence_store = lambda profile=None: {"profile_id": getattr(profile, "profile_id", None)}
    investigation_module.build_unsupported_profile_report = lambda *_args, **_kwargs: "unsupported"
    investigation_module.decide_stop_action = lambda **_kwargs: types.SimpleNamespace(model_dump=lambda: {"decision": "finalize_with_limitations"})
    investigation_module.get_profile = lambda profile_id: types.SimpleNamespace(profile_id=profile_id)
    investigation_module.get_runtime = lambda profile_id: _DummyRuntime() if profile_id == "memory_pressure_profile" else None
    investigation_module.supports_profile_execution = lambda *_args, **_kwargs: True
    sys.modules["app.agent.aiops.investigation"] = investigation_module

    patrol_module = types.ModuleType("app.agent.aiops.patrol")
    patrol_module.summarize_alerts = lambda *_args, **_kwargs: "alerts"
    sys.modules["app.agent.aiops.patrol"] = patrol_module

    profile_loader_module = types.ModuleType("app.agent.aiops.profile_loader")
    profile_loader_module.get_agent_profile_prompt = lambda: "profile prompt"
    sys.modules["app.agent.aiops.profile_loader"] = profile_loader_module

    state_module = types.ModuleType("app.agent.aiops.state")
    state_module.PlanExecuteState = dict
    sys.modules["app.agent.aiops.state"] = state_module

    tool_registry_module = types.ModuleType("app.agent.aiops.tool_registry")
    tool_registry_module.get_aiops_local_tools = lambda: []
    sys.modules["app.agent.aiops.tool_registry"] = tool_registry_module

    trace_module = types.ModuleType("app.agent.aiops.trace")
    trace_module.create_trace_event = lambda **kwargs: kwargs
    trace_module.summarize_result = lambda payload: str(payload)
    sys.modules["app.agent.aiops.trace"] = trace_module

    utils_module = types.ModuleType("app.agent.aiops.utils")
    async def _invoke_tool(*_args, **_kwargs):
        return {}
    utils_module.format_tools_description = lambda *_args, **_kwargs: "tools"
    utils_module.invoke_tool = _invoke_tool
    sys.modules["app.agent.aiops.utils"] = utils_module

    mcp_module = types.ModuleType("app.agent.mcp_client")
    async def _get_mcp_client_with_retry():
        return _DummyMcpClient()
    mcp_module.get_mcp_client_with_retry = _get_mcp_client_with_retry
    sys.modules["app.agent.mcp_client"] = mcp_module

    config_module = types.ModuleType("app.config")
    config_module.config = types.SimpleNamespace(rag_model="qwen-max")
    sys.modules["app.config"] = config_module

    llm_factory_module = types.ModuleType("app.core.llm_factory")
    llm_factory_module.llm_factory = types.SimpleNamespace(create_qwen_chat_model=lambda **_kwargs: object())
    sys.modules["app.core.llm_factory"] = llm_factory_module

    tools_module = types.ModuleType("app.tools")
    tools_module.retrieve_knowledge = object()
    sys.modules["app.tools"] = tools_module

    try:
        spec = spec_from_file_location("planner_session_memory_test_module", PLANNER_PATH)
        assert spec and spec.loader
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in backups.items():
            if original is not None:
                sys.modules[name] = original
            else:
                sys.modules.pop(name, None)


def test_planner_references_session_memory_for_independent_runtime_path():
    planner_module = _load_planner_module()
    state = {
        "input": "系统现在内存情况如何？",
        "mode": "custom",
        "session_id": "test-session-memory-ref",
        "matched_skills": [],
        "diagnosis_intent": "status_query",
        "selected_profile": {"profile_id": "memory_pressure_profile"},
        "active_alerts": [],
        "target_alert": None,
        "previous_aiops_context": {},
        "followup_relation": {"relation_type": "independent"},
        "session_long_term_summary": "之前同一主机出现过内存抖动，但历史结论不能替代当前证据。",
        "session_recent_turns": [
            {
                "user_input": "CPU 满了怎么办",
                "selected_profile": "cpu_pressure_profile",
                "tools_used": ["get_cpu_summary"],
                "final_report_summary": "上一轮 CPU 压力较高。",
                "risk_events": ["restart_service requires approval"],
            }
        ],
    }

    result = asyncio.run(planner_module.planner(state))
    trace_titles = [event.get("title") for event in result.get("trace_events", []) if isinstance(event, dict)]
    assert "Session memory referenced by planner" in trace_titles
    assert result["plan_source"] == "investigation_runtime"
    assert [step["tool"] for step in result["plan"]] == ["get_memory_summary", "list_top_memory_processes"]
