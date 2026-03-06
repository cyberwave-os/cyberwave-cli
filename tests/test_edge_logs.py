import importlib
import sys
from types import ModuleType, SimpleNamespace

edge_module = importlib.import_module("cyberwave_cli.commands.edge")


def test_show_logs_uses_clean_env_and_service_name(monkeypatch):
    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {"LD_LIBRARY_PATH": "/usr/lib"}

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)

    calls = {}

    def _fake_run(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(edge_module.subprocess, "run", _fake_run)

    edge_module.show_logs.callback(follow=True, lines=123)

    assert calls["command"] == [
        "journalctl",
        "-u",
        "cyberwave-edge-core",
        "-n123",
        "--no-pager",
        "--output=cat",
        "-f",
    ]
    assert calls["kwargs"]["env"] == {"LD_LIBRARY_PATH": "/usr/lib"}
    assert calls["kwargs"]["check"] is False


def test_show_logs_prints_sudo_tip_on_non_zero_exit(monkeypatch):
    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)

    monkeypatch.setattr(
        edge_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=1),
    )

    printed = []
    monkeypatch.setattr(
        edge_module.console,
        "print",
        lambda message="", *args, **kwargs: printed.append(str(message)),
    )

    edge_module.show_logs.callback(follow=False, lines=50)

    assert any("run with sudo" in message for message in printed)
