import sys
import types


class _FakeLogger:
    def add(self, *args, **kwargs):
        return 0

    def remove(self, *args, **kwargs):
        return None

    def __getattr__(self, _name):
        def _noop(*args, **kwargs):
            return None
        return _noop


fake_loguru = types.ModuleType("loguru")
fake_loguru.logger = _FakeLogger()
sys.modules.setdefault("loguru", fake_loguru)
