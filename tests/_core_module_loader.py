import importlib
import sys
from pathlib import Path
from types import ModuleType


def load_core_module(monkeypatch):
    """Import `cyberwave_cli.core` with lightweight dependency stubs."""
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
    cli_config_module.CONFIG_DIR = Path("/tmp/cyberwave-config")
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
