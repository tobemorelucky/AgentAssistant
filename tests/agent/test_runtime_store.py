from app.agent.aiops import runtime_store as runtime_store_module


def test_runtime_store_round_trip(tmp_path, monkeypatch):
    runtime_dir = tmp_path / "runtime"
    pending_dir = tmp_path / "pending"
    incident_dir = tmp_path / "incident"

    monkeypatch.setattr(runtime_store_module, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(runtime_store_module, "PENDING_DIR", pending_dir)
    monkeypatch.setattr(runtime_store_module, "INCIDENT_DIR", incident_dir)

    store = runtime_store_module.RuntimeStore()
    store.save_session("session-1", {"plan": ["step-1"]}, "running")
    store.save_pending_action("session-1", {"action_id": "a1", "status": "pending"})

    snapshot = store.load_session("session-1")
    pending = store.load_pending_actions("session-1")

    assert snapshot["status"] == "running"
    assert snapshot["state"]["plan"] == ["step-1"]
    assert pending["actions"][0]["action_id"] == "a1"
