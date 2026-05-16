import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL_LOADER_PATH = ROOT / "app" / "agent" / "aiops" / "skill_loader.py"
SKILL_ROUTER_PATH = ROOT / "app" / "agent" / "aiops" / "skill_router.py"
INVESTIGATION_MODELS_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "models.py"
INVESTIGATION_PROFILES_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "profiles.py"
INVESTIGATION_EVIDENCE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "evidence.py"


def _load_module(module_name: str, path: Path):
    spec = spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_skill_modules():
    original_modules = {name: sys.modules.get(name) for name in list(sys.modules)}

    def restore():
        targets = [
            "app",
            "app.agent",
            "app.agent.aiops",
            "app.agent.aiops.skill_loader",
            "app.agent.aiops.skill_router",
            "app.agent.aiops.state",
            "app.agent.aiops.trace",
            "app.agent.aiops.investigation",
            "app.agent.aiops.investigation.models",
            "app.agent.aiops.investigation.profiles",
            "app.agent.aiops.investigation.evidence",
        ]
        for name in targets:
            original = original_modules.get(name)
            if original is not None:
                sys.modules[name] = original
            else:
                sys.modules.pop(name, None)

    fake_app = types.ModuleType("app")
    fake_app.__path__ = []  # type: ignore[attr-defined]
    fake_agent = types.ModuleType("app.agent")
    fake_agent.__path__ = []  # type: ignore[attr-defined]
    fake_aiops = types.ModuleType("app.agent.aiops")
    fake_aiops.__path__ = []  # type: ignore[attr-defined]

    sys.modules["app"] = fake_app
    sys.modules["app.agent"] = fake_agent
    sys.modules["app.agent.aiops"] = fake_aiops

    models = _load_module("app.agent.aiops.investigation.models", INVESTIGATION_MODELS_PATH)
    profiles = _load_module("app.agent.aiops.investigation.profiles", INVESTIGATION_PROFILES_PATH)
    evidence = _load_module("app.agent.aiops.investigation.evidence", INVESTIGATION_EVIDENCE_PATH)

    investigation = types.ModuleType("app.agent.aiops.investigation")
    investigation.infer_diagnosis_intent = profiles.infer_diagnosis_intent
    investigation.resolve_selected_profile = profiles.resolve_selected_profile
    investigation.build_evidence_store = evidence.build_evidence_store
    sys.modules["app.agent.aiops.investigation"] = investigation

    state = types.ModuleType("app.agent.aiops.state")
    state.PlanExecuteState = dict
    sys.modules["app.agent.aiops.state"] = state

    trace = types.ModuleType("app.agent.aiops.trace")
    trace.create_trace_event = lambda **kwargs: kwargs
    sys.modules["app.agent.aiops.trace"] = trace

    skill_loader = _load_module("app.agent.aiops.skill_loader", SKILL_LOADER_PATH)
    skill_router = _load_module("app.agent.aiops.skill_router", SKILL_ROUTER_PATH)
    return skill_loader, skill_router, restore


skill_loader, skill_router, _restore = _load_skill_modules()


def teardown_module():
    _restore()


def test_infer_intents_matches_cpu_and_logs():
    intents = skill_router.infer_intents("please diagnose high cpu and check error logs")
    assert "cpu_diagnosis" in intents
    assert "log_analysis" in intents


def test_infer_intents_matches_disk_cleanup():
    intents = skill_router.infer_intents("服务器磁盘使用率过高，怀疑硬盘满了，请给出清理建议")
    assert "disk_diagnosis" in intents


def test_score_skill_prefers_service_and_alert_matches():
    skill = skill_loader.SkillDefinition(
        name="CPU Runbook",
        description="diagnose cpu incidents",
        trigger={
            "services": ["checkout"],
            "alerts": ["HighCPUUsage"],
            "keywords": ["cpu"],
            "intents": ["cpu_diagnosis"],
        },
    )

    score, reasons = skill_router.score_skill(
        "checkout service has HighCPUUsage and cpu keeps rising",
        skill,
    )

    assert score >= 100
    assert "service" in reasons
    assert "alert" in reasons
    assert "keyword" in reasons
    assert "intent" in reasons


def test_match_skills_limits_to_top_three(monkeypatch):
    skills = [
        skill_loader.SkillDefinition(name=f"skill-{index}", description="x", trigger={"keywords": [f"hit{index}"]})
        for index in range(5)
    ]
    monkeypatch.setattr(skill_router, "load_skills", lambda: skills)

    result = skill_router.match_skills("hit0 hit1 hit2 hit3 hit4", limit=3)

    assert len(result) == 3


def test_match_skills_hits_disk_cleanup():
    result = skill_router.match_skills("请检查服务器当前磁盘空间使用情况，并分析主要占用来源。", limit=3)
    matched = next(skill for skill in result if skill["name"] == "disk_cleanup")
    assert matched["skill_mode"] == "execution_profile"
    assert matched["profile_id"] == "disk_pressure_profile"


def test_skill_definition_defaults_to_reference_playbook():
    skill = skill_loader.SkillDefinition(name="legacy", description="legacy skill")
    assert skill.skill_mode == "reference_playbook"
