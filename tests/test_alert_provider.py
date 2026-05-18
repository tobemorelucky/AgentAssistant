import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "monitoring" / "alert_provider.py"


def _load_alert_provider_module():
    original_modules = {name: sys.modules.get(name) for name in list(sys.modules)}

    fake_app = types.ModuleType("app")
    fake_app.__path__ = []  # type: ignore[attr-defined]
    fake_monitoring = types.ModuleType("app.monitoring")
    fake_monitoring.__path__ = []  # type: ignore[attr-defined]
    fake_config_module = types.ModuleType("app.config")
    fake_config_module.config = types.SimpleNamespace(aiops_alert_provider="mock")
    fake_monitor_provider = types.ModuleType("app.monitoring.monitor_provider")
    fake_monitor_provider.get_cpu_summary_data = lambda: {}
    fake_monitor_provider.get_memory_summary_data = lambda: {}
    fake_monitor_provider.get_disk_usage_data = lambda mount="/": {}

    sys.modules["app"] = fake_app
    sys.modules["app.monitoring"] = fake_monitoring
    sys.modules["app.config"] = fake_config_module
    sys.modules["app.monitoring.monitor_provider"] = fake_monitor_provider

    try:
        spec = spec_from_file_location("test_alert_provider_module", MODULE_PATH)
        assert spec and spec.loader
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name, module in original_modules.items():
            if module is not None:
                sys.modules[name] = module
        for name in ["app", "app.monitoring", "app.config", "app.monitoring.monitor_provider"]:
            if name not in original_modules:
                sys.modules.pop(name, None)


alert_provider = _load_alert_provider_module()


def test_alert_provider_name_defaults_to_mock():
    alert_provider.config.aiops_alert_provider = ""
    assert alert_provider.get_alert_provider_name() == "mock"


def test_build_remote_host_alerts_generates_host_level_cpu_alert(monkeypatch):
    alert_provider.config.aiops_alert_provider = "remote_host"
    monkeypatch.setattr(
        alert_provider,
        "get_cpu_summary_data",
        lambda: {"ok": True, "host": "he-VMware-Virtual-Platform", "status": "warning", "source": "remote_host"},
    )
    monkeypatch.setattr(
        alert_provider,
        "get_memory_summary_data",
        lambda: {"ok": True, "host": "he-VMware-Virtual-Platform", "status": "healthy", "source": "remote_host"},
    )
    monkeypatch.setattr(
        alert_provider,
        "get_disk_usage_data",
        lambda mount="/": {"ok": True, "host": "he-VMware-Virtual-Platform", "status": "healthy", "source": "remote_host"},
    )

    result = alert_provider.build_remote_host_alerts()

    assert result["provider"] == "remote_host"
    assert result["total"] == 1
    assert result["active_alerts"][0]["alert_name"] == "HostHighCPUUsage"
    assert result["active_alerts"][0]["host"] == "he-VMware-Virtual-Platform"


def test_build_remote_host_alerts_returns_empty_when_host_is_healthy(monkeypatch):
    monkeypatch.setattr(
        alert_provider,
        "get_cpu_summary_data",
        lambda: {"ok": True, "host": "he-VMware-Virtual-Platform", "status": "healthy", "source": "remote_host"},
    )
    monkeypatch.setattr(
        alert_provider,
        "get_memory_summary_data",
        lambda: {"ok": True, "host": "he-VMware-Virtual-Platform", "status": "healthy", "source": "remote_host"},
    )
    monkeypatch.setattr(
        alert_provider,
        "get_disk_usage_data",
        lambda mount="/": {"ok": True, "host": "he-VMware-Virtual-Platform", "status": "healthy", "source": "remote_host"},
    )

    result = alert_provider.build_remote_host_alerts()

    assert result["total"] == 0
    assert result["active_alerts"] == []


def test_disabled_alert_result_is_controlled():
    result = alert_provider.build_disabled_alert_result()
    assert result["provider"] == "disabled"
    assert result["total"] == 0
