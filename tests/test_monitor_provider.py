from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "monitoring" / "monitor_provider.py"
SPEC = spec_from_file_location("test_monitor_provider_module", MODULE_PATH)
assert SPEC and SPEC.loader
monitor_provider = module_from_spec(SPEC)
SPEC.loader.exec_module(monitor_provider)


def test_monitor_provider_defaults_to_mock(monkeypatch):
    monkeypatch.delenv("AIOPS_MONITOR_PROVIDER", raising=False)

    result = monitor_provider.get_disk_usage_data(hostname="demo-server-01", mount="/")

    assert result["source"] == "mock"
    assert result["host"] == "demo-server-01"
    assert result["mount"] == "/"
    assert result["usage_percent"] == 92.4


def test_remote_host_disk_usage_adapts_success(monkeypatch):
    monkeypatch.setenv("AIOPS_MONITOR_PROVIDER", "remote_host")
    monkeypatch.setenv("AIOPS_REMOTE_HOST_BASE_URL", "http://192.168.6.129:9001")

    def fake_request(path, params=None):
        assert path == "/api/v1/disk/usage"
        assert params == {"mount": "/"}
        return 200, {
            "host": "he-VMware-Virtual-Platform",
            "mount": "/",
            "total_gb": 39.07,
            "used_gb": 16.05,
            "available_gb": 21.01,
            "usage_percent": 41.1,
            "status": "healthy",
        }

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.get_disk_usage_data(mount="/")

    assert result["source"] == "remote_host"
    assert result["host"] == "he-VMware-Virtual-Platform"
    assert result["usage_percent"] == 41.1
    assert result["status"] == "healthy"


def test_remote_host_timeout_returns_structured_error(monkeypatch):
    monkeypatch.setenv("AIOPS_MONITOR_PROVIDER", "remote_host")
    monkeypatch.setenv("AIOPS_REMOTE_HOST_BASE_URL", "http://192.168.6.129:9001")

    def fake_request(path, params=None):
        raise monitor_provider.httpx.TimeoutException("timeout")

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.get_disk_usage_data(mount="/")

    assert result["ok"] is False
    assert result["source"] == "remote_host"
    assert result["error_code"] == "remote_host_timeout"
    assert result["message"]


def test_remote_host_docker_error_is_non_fatal(monkeypatch):
    monkeypatch.setenv("AIOPS_MONITOR_PROVIDER", "remote_host")
    monkeypatch.setenv("AIOPS_REMOTE_HOST_BASE_URL", "http://192.168.6.129:9001")

    def fake_request(path, params=None):
        assert path == "/api/v1/docker/disk-usage"
        return 200, {"ok": False, "message": "docker service unavailable", "error_code": "docker_unavailable"}

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.query_docker_disk_usage_data()

    assert result["ok"] is False
    assert result["source"] == "remote_host"
    assert result["error_code"] == "docker_unavailable"
