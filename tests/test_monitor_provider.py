import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "monitoring" / "monitor_provider.py"


def _load_monitor_provider_module():
    original_app = sys.modules.get("app")
    original_app_config = sys.modules.get("app.config")

    fake_app_module = types.ModuleType("app")
    fake_app_module.__path__ = []  # type: ignore[attr-defined]
    fake_config_module = types.ModuleType("app.config")
    fake_config_module.config = types.SimpleNamespace(
        aiops_monitor_provider="mock",
        aiops_remote_host_base_url="",
        aiops_remote_host_token="",
    )

    sys.modules["app"] = fake_app_module
    sys.modules["app.config"] = fake_config_module

    try:
        spec = spec_from_file_location("test_monitor_provider_module", MODULE_PATH)
        assert spec and spec.loader
        module = module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if original_app is not None:
            sys.modules["app"] = original_app
        else:
            sys.modules.pop("app", None)

        if original_app_config is not None:
            sys.modules["app.config"] = original_app_config
        else:
            sys.modules.pop("app.config", None)


monitor_provider = _load_monitor_provider_module()


def test_monitor_provider_defaults_to_mock():
    monitor_provider.config.aiops_monitor_provider = "mock"
    monitor_provider.config.aiops_remote_host_base_url = ""
    monitor_provider.config.aiops_remote_host_token = ""

    result = monitor_provider.get_disk_usage_data(hostname="demo-server-01", mount="/")

    assert result["source"] == "mock"
    assert result["host"] == "demo-server-01"
    assert result["mount"] == "/"
    assert result["usage_percent"] == 92.4


def test_remote_host_disk_usage_adapts_success(monkeypatch):
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

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
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

    def fake_request(path, params=None):
        raise monitor_provider.httpx.TimeoutException("timeout")

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.get_disk_usage_data(mount="/")

    assert result["ok"] is False
    assert result["source"] == "remote_host"
    assert result["error_code"] == "remote_host_timeout"
    assert result["message"]


def test_remote_host_docker_error_is_non_fatal(monkeypatch):
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

    def fake_request(path, params=None):
        assert path == "/api/v1/docker/disk-usage"
        return 200, {"ok": False, "message": "docker service unavailable", "error_code": "docker_unavailable"}

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.query_docker_disk_usage_data()

    assert result["ok"] is False
    assert result["source"] == "remote_host"
    assert result["error_code"] == "docker_unavailable"


def test_remote_host_large_files_adapts_success(monkeypatch):
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

    def fake_request(path, params=None):
        assert path == "/api/v1/disk/large-files"
        assert params == {"path": "/", "min_size_mb": 100, "limit": 20}
        return 200, {
            "ok": True,
            "files": [
                {"path": "/swap.img", "size_mb": 4096.0, "size_gb": 4.0, "scan_root": "/", "warning": ""},
                {"path": "/var/log/syslog", "size_mb": 512.0, "scan_root": "/", "warning": "rotating soon"},
            ],
            "warnings": [],
            "scan_root": "/",
            "min_size_mb": 100,
            "limit": 20,
            "scan_incomplete": True,
            "skipped_paths": ["/proc"],
            "skipped_count": 1,
            "permission_denied_count": 2,
        }

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.list_large_files_data(path="/", min_size_mb=100, limit=20)

    assert result["ok"] is True
    assert result["source"] == "remote_host"
    assert result["files"][0]["path"] == "/swap.img"
    assert result["files"][0]["size_gb"] == 4.0
    assert result["files"][1]["size_gb"] == 0.5
    assert result["scan_incomplete"] is True
    assert result["permission_denied_count"] == 2


def test_remote_host_deleted_open_files_adapts_success(monkeypatch):
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

    def fake_request(path, params=None):
        assert path == "/api/v1/disk/deleted-open-files"
        assert params is None
        return 200, {
            "ok": True,
            "message": "filtered",
            "files": [],
            "total": 0,
            "total_raw": 5,
            "total_filtered": 0,
            "filtered_out_count": 5,
            "filters_applied": ["exclude_memfd", "min_size_mb>=10"],
        }

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.query_deleted_open_files_data()

    assert result["ok"] is True
    assert result["source"] == "remote_host"
    assert result["filtered_out_count"] == 5
    assert result["filters_applied"] == ["exclude_memfd", "min_size_mb>=10"]


def test_remote_host_large_files_structured_error_is_non_fatal(monkeypatch):
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

    def fake_request(path, params=None):
        return 200, {"ok": False, "message": "scan failed", "error_code": "scan_failed"}

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.list_large_files_data(path="/", min_size_mb=100, limit=20)

    assert result["ok"] is False
    assert result["source"] == "remote_host"
    assert result["error_code"] == "scan_failed"


def test_mock_memory_summary_works():
    monitor_provider.config.aiops_monitor_provider = "mock"
    monitor_provider.config.aiops_remote_host_base_url = ""
    monitor_provider.config.aiops_remote_host_token = ""

    result = monitor_provider.get_memory_summary_data()

    assert result["source"] == "mock"
    assert result["usage_percent"] == 86.3
    assert result["host"] == "demo-server-01"


def test_remote_host_memory_summary_adapts_success(monkeypatch):
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

    def fake_request(path, params=None):
        assert path == "/api/v1/system/memory-summary"
        assert params is None
        return 200, {
            "ok": True,
            "host": "he-VMware-Virtual-Platform",
            "total_gb": 15.52,
            "used_gb": 7.24,
            "available_gb": 8.28,
            "usage_percent": 46.7,
            "status": "healthy",
        }

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.get_memory_summary_data()

    assert result["source"] == "remote_host"
    assert result["usage_percent"] == 46.7
    assert result["available_gb"] == 8.28


def test_remote_host_top_memory_processes_adapts_success(monkeypatch):
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

    def fake_request(path, params=None):
        assert path == "/api/v1/process/top-memory"
        assert params == {"limit": 10}
        return 200, {
            "ok": True,
            "limit": 10,
            "processes": [
                {
                    "pid": 123,
                    "process_name": "python",
                    "command": "python worker.py",
                    "memory_percent": 22.4,
                    "rss_mb": 1024.0,
                }
            ],
        }

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.list_top_memory_processes_data(limit=10)

    assert result["ok"] is True
    assert result["source"] == "remote_host"
    assert result["processes"][0]["process_name"] == "python"
    assert result["processes"][0]["rss_gb"] == 1.0


def test_remote_host_cpu_summary_adapts_success(monkeypatch):
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

    def fake_request(path, params=None):
        assert path == "/api/v1/system/cpu-summary"
        assert params is None
        return 200, {
            "ok": True,
            "host": "he-VMware-Virtual-Platform",
            "usage_percent": 38.5,
            "cores": 4,
            "load_1": 0.82,
            "load_5": 0.74,
            "load_15": 0.65,
            "status": "healthy",
        }

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.get_cpu_summary_data()

    assert result["source"] == "remote_host"
    assert result["usage_percent"] == 38.5
    assert result["load_1"] == 0.82


def test_remote_host_top_cpu_processes_adapts_success(monkeypatch):
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

    def fake_request(path, params=None):
        assert path == "/api/v1/process/top-cpu"
        assert params == {"limit": 10}
        return 200, {
            "ok": True,
            "limit": 10,
            "processes": [
                {
                    "pid": 889,
                    "process_name": "java",
                    "command": "java -jar app.jar",
                    "cpu_percent": 41.2,
                    "threads": 32,
                }
            ],
        }

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.list_top_cpu_processes_data(limit=10)

    assert result["ok"] is True
    assert result["source"] == "remote_host"
    assert result["processes"][0]["cpu_percent"] == 41.2
    assert result["processes"][0]["threads"] == 32


def test_remote_host_cpu_summary_structured_error_is_non_fatal(monkeypatch):
    monitor_provider.config.aiops_monitor_provider = "remote_host"
    monitor_provider.config.aiops_remote_host_base_url = "http://192.168.6.129:9001"
    monitor_provider.config.aiops_remote_host_token = ""

    def fake_request(path, params=None):
        return 200, {"ok": False, "message": "cpu summary unavailable", "error_code": "cpu_unavailable"}

    monkeypatch.setattr(monitor_provider, "_request_remote_json", fake_request)

    result = monitor_provider.get_cpu_summary_data()

    assert result["ok"] is False
    assert result["source"] == "remote_host"
    assert result["error_code"] == "cpu_unavailable"
