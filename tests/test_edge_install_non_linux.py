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

    rich_module = ModuleType("rich")
    rich_console_module = ModuleType("rich.console")
    rich_prompt_module = ModuleType("rich.prompt")

    class _Console:
        def print(self, *_args, **_kwargs):
            return None

    class _Confirm:
        @staticmethod
        def ask(*_args, **_kwargs):
            return True

    class _Prompt:
        @staticmethod
        def ask(*_args, **_kwargs):
            return ""

    rich_console_module.Console = _Console
    rich_prompt_module.Confirm = _Confirm
    rich_prompt_module.Prompt = _Prompt
    rich_module.console = rich_console_module
    rich_module.prompt = rich_prompt_module

    auth_module = ModuleType("cyberwave_cli.auth")
    auth_module.APIToken = object
    auth_module.AuthClient = object
    auth_module.AuthenticationError = Exception

    cli_config_module = ModuleType("cyberwave_cli.config")
    cli_config_module.CONFIG_DIR = ModuleType("dummy").__class__("dummy")  # placeholder
    cli_config_module.CONFIG_DIR = __import__("pathlib").Path("/tmp/cyberwave-config")
    cli_config_module.clean_subprocess_env = lambda: {}
    cli_config_module.get_api_url = lambda: "https://api.example.test"

    credentials_module = ModuleType("cyberwave_cli.credentials")
    credentials_module.Credentials = object
    credentials_module.collect_runtime_env_overrides = lambda *args, **kwargs: {}
    credentials_module.load_credentials = lambda: None
    credentials_module.save_credentials = lambda *_args, **_kwargs: None

    monkeypatch.setitem(sys.modules, "cyberwave", cyberwave_module)
    monkeypatch.setitem(sys.modules, "cyberwave.config", config_module)
    monkeypatch.setitem(sys.modules, "cyberwave.fingerprint", fingerprint_module)
    monkeypatch.setitem(sys.modules, "rich", rich_module)
    monkeypatch.setitem(sys.modules, "rich.console", rich_console_module)
    monkeypatch.setitem(sys.modules, "rich.prompt", rich_prompt_module)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.auth", auth_module)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", cli_config_module)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", credentials_module)

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
    monkeypatch.setattr(
        core,
        "install_edge_core",
        lambda *, channel, version: calls.append(("install", (channel, version))) or True,
    )
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
    assert calls == [("credentials", True), ("install", ("stable", None)), ("configure", True)]
    assert any(
        "Edge core service setup is only supported on Linux. "
        "You will to start the core manually upon restart" in message
        for message in messages
    )


def test_setup_edge_core_non_linux_returns_false_when_config_fails(monkeypatch):
    core = _load_core_module(monkeypatch)

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "install_edge_core", lambda *, channel, version: True)
    monkeypatch.setattr(core, "configure_edge_environment", lambda *, skip_confirm: False)

    assert core.setup_edge_core(skip_confirm=True) is False
