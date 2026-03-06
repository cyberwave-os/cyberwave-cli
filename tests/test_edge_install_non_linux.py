import importlib
import sys
from types import ModuleType


def _load_core_module(monkeypatch):
    """Import cyberwave_cli.core with lightweight cyberwave stubs."""
    cyberwave_module = ModuleType("cyberwave")

    config_module = ModuleType("cyberwave.config")
    config_module.DEFAULT_BASE_URL = "https://api.example.test"

    fingerprint_module = ModuleType("cyberwave.fingerprint")
    fingerprint_module.generate_fingerprint = lambda: "fingerprint-test"

    cyberwave_module.config = config_module
    cyberwave_module.fingerprint = fingerprint_module

    monkeypatch.setitem(sys.modules, "cyberwave", cyberwave_module)
    monkeypatch.setitem(sys.modules, "cyberwave.config", config_module)
    monkeypatch.setitem(sys.modules, "cyberwave.fingerprint", fingerprint_module)

    sys.modules.pop("cyberwave_cli.config", None)
    sys.modules.pop("cyberwave_cli.core", None)
    return importlib.import_module("cyberwave_cli.core")


def test_setup_edge_core_non_linux_continues_without_service_setup(monkeypatch):
    core = _load_core_module(monkeypatch)

    calls: list[tuple[str, bool | None]] = []
    messages: list[str] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(
        core,
        "_ensure_credentials",
        lambda *, skip_confirm: calls.append(("credentials", skip_confirm)) or True,
    )
    monkeypatch.setattr(core, "install_edge_core", lambda: calls.append(("install", None)) or True)
    monkeypatch.setattr(
        core,
        "configure_edge_environment",
        lambda *, skip_confirm: calls.append(("configure", skip_confirm)) or True,
    )
    monkeypatch.setattr(core, "_install_docker", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(core, "create_systemd_service", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(
        core,
        "enable_and_start_service",
        lambda: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(core.console, "print", lambda message="", *args, **kwargs: messages.append(str(message)))

    assert core.setup_edge_core(skip_confirm=True) is True
    assert calls == [("credentials", True), ("install", None), ("configure", True)]
    assert any(
        "Edge core service setup is only supported on Linux. "
        "You will to start the core manually upon restart" in message
        for message in messages
    )


def test_setup_edge_core_non_linux_returns_false_when_config_fails(monkeypatch):
    core = _load_core_module(monkeypatch)

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "install_edge_core", lambda: True)
    monkeypatch.setattr(core, "configure_edge_environment", lambda *, skip_confirm: False)

    assert core.setup_edge_core(skip_confirm=True) is False
