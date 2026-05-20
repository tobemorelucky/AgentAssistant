import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODELS_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "models.py"
PROFILES_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "profiles.py"
EVIDENCE_PATH = ROOT / "app" / "agent" / "aiops" / "investigation" / "evidence.py"


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


def test_patrol_dispatch_profile_is_no_longer_registered_for_execution():
    assert profiles.get_profile("patrol_dispatch_profile") is None
    assert profiles.supports_profile_execution("patrol_dispatch_profile") is False


def test_default_patrol_profile_is_host_health_runtime_profile():
    profile = profiles.resolve_selected_profile(mode="default", matched_skills=[])
    assert profile is not None
    assert profile.profile_id == "host_health_patrol_profile"
    store = evidence.build_evidence_store(profile)
    assert set(store) == {"cpu_summary", "memory_summary", "disk_usage", "active_alerts"}


def test_custom_reference_playbook_only_request_does_not_resolve_execution_profile():
    profile = profiles.resolve_selected_profile(
        mode="custom",
        matched_skills=[{"name": "High Memory Diagnosis", "skill_mode": "reference_playbook", "profile_id": None}],
    )
    assert profile is None
    intent = profiles.infer_diagnosis_intent(
        mode="custom",
        input_text="内存满了怎么办",
        matched_skills=[{"name": "High Memory Diagnosis", "skill_mode": "reference_playbook", "profile_id": None}],
    )
    assert intent in {models.DiagnosisIntent.REMEDIATION_REQUEST, models.DiagnosisIntent.INCIDENT_DIAGNOSIS}
