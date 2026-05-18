import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODELS_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "models.py"
PROFILES_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "profiles.py"
EVIDENCE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "evidence.py"
STOP_CONTROLLER_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "stop_controller.py"


def _load_module(module_name: str, path: Path):
    spec = spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


models = _load_module("app.agent.aiops.investigation.models", MODELS_PATH)
profiles = _load_module("app.agent.aiops.investigation.profiles", PROFILES_PATH)
evidence = _load_module("app.agent.aiops.investigation.evidence", EVIDENCE_PATH)
stop_controller = _load_module("app.agent.aiops.investigation.stop_controller", STOP_CONTROLLER_PATH)


def test_default_patrol_profile_is_registered():
    profile = profiles.resolve_selected_profile(mode="default", matched_skills=[])
    assert profile is not None
    assert profile.profile_id == "host_health_patrol_profile"
    assert profiles.supports_profile_execution(profile.profile_id) is True


def test_disk_pressure_profile_is_registered():
    profile = profiles.resolve_selected_profile(
        mode="custom",
        matched_skills=[{"name": "disk_cleanup", "skill_mode": "execution_profile", "profile_id": "disk_pressure_profile"}],
    )
    assert profile is not None
    assert profile.profile_id == "disk_pressure_profile"
    assert profiles.supports_profile_execution(profile.profile_id) is True


def test_missing_execution_profile_defaults_to_knowledge_only_or_reference_path():
    intent = profiles.infer_diagnosis_intent(
        mode="custom",
        input_text="内存满了怎么办",
        matched_skills=[{"name": "High Memory Diagnosis", "skill_mode": "reference_playbook", "profile_id": None}],
    )
    assert intent in {models.DiagnosisIntent.REMEDIATION_REQUEST, models.DiagnosisIntent.INCIDENT_DIAGNOSIS}
    assert profiles.supports_profile_execution("high_memory_diagnosis") is False


def test_evidence_store_starts_empty_for_default_patrol():
    store = evidence.build_evidence_store(profiles.DEFAULT_PATROL_PROFILE)
    assert {"cpu_summary", "memory_summary", "disk_usage", "active_alerts"} <= set(store)
    assert store["cpu_summary"]["status"] == models.EvidenceStatus.MISSING


def test_stop_controller_finalizes_with_limitations_after_no_progress():
    decision = stop_controller.decide_stop_action(
        profile=profiles.DEFAULT_PATROL_PROFILE,
        no_progress_rounds=2,
        reason="No execution progress.",
        missing_slots=["metric"],
    )
    assert decision.decision == models.StopDecisionType.FINALIZE_WITH_LIMITATIONS
    assert decision.missing_slots == ["metric"]
