import importlib
import sys
from types import ModuleType


def _load_core_module(monkeypatch):
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


def test_install_edge_core_defaults_to_stable_package(monkeypatch):
    core = _load_core_module(monkeypatch)
    calls = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    monkeypatch.setattr(
        core,
        "_apt_get_install",
        lambda *, package_name, package_version: calls.append((package_name, package_version)) or True,
    )

    assert core.install_edge_core() is True
    assert calls == [("cyberwave-edge-core", None)]


def test_install_edge_core_uses_selected_channel_and_version(monkeypatch):
    core = _load_core_module(monkeypatch)
    calls = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    monkeypatch.setattr(
        core,
        "_apt_get_install",
        lambda *, package_name, package_version: calls.append((package_name, package_version)) or True,
    )

    assert (
        core.install_edge_core(channel="staging", version="0.0.42.595") is True
    )
    assert calls == [("cyberwave-edge-core-staging", "0.0.42.595")]


def test_install_edge_core_rejects_non_stable_channel_without_apt(monkeypatch):
    core = _load_core_module(monkeypatch)
    messages = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core.console, "print", lambda message="", *args, **kwargs: messages.append(str(message)))

    assert core.install_edge_core(channel="dev") is False
    assert any("Non-stable edge-core channels are only supported via apt-get" in message for message in messages)
