import importlib
import sys
from types import ModuleType, SimpleNamespace

edge_module = importlib.import_module("cyberwave_cli.commands.edge")


class _FakePopen:
    """Minimal Popen stand-in that records the constructor args and yields
    pre-canned stdout lines."""

    instances: list["_FakePopen"] = []

    def __init__(self, command, **kwargs):
        self.command = command
        self.kwargs = kwargs
        self.returncode = 0
        self.stdout = iter([])
        _FakePopen.instances.append(self)

    def terminate(self):
        pass

    def wait(self):
        pass


def test_show_logs_uses_clean_env_and_service_name(monkeypatch):
    _FakePopen.instances.clear()

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {"LD_LIBRARY_PATH": "/usr/lib"}

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core._migrate_legacy_config_dir = lambda: None

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setattr(edge_module.sys, "platform", "linux")
    monkeypatch.setattr(edge_module.subprocess, "Popen", _FakePopen)

    edge_module.show_logs.callback(follow=True, lines=123)

    assert len(_FakePopen.instances) == 1
    proc = _FakePopen.instances[0]

    assert proc.command == [
        "journalctl",
        "-u",
        "cyberwave-edge-core",
        "-n123",
        "--no-pager",
        "--output=cat",
        "-f",
    ]
    assert proc.kwargs["env"] == {"LD_LIBRARY_PATH": "/usr/lib"}


def test_show_logs_prints_sudo_tip_on_non_zero_exit(monkeypatch):
    _FakePopen.instances.clear()

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core._migrate_legacy_config_dir = lambda: None

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setattr(edge_module.sys, "platform", "linux")

    class _FailPopen(_FakePopen):
        def __init__(self, command, **kwargs):
            super().__init__(command, **kwargs)
            self.returncode = 1

    monkeypatch.setattr(edge_module.subprocess, "Popen", _FailPopen)

    printed = []
    monkeypatch.setattr(
        edge_module.console,
        "print",
        lambda message="", *args, **kwargs: printed.append(str(message)),
    )

    edge_module.show_logs.callback(follow=False, lines=50)

    assert any("run with sudo" in message for message in printed)
