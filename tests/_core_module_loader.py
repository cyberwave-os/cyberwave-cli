import importlib
import importlib.util
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

    edge_module = ModuleType("cyberwave.edge")
    edge_module.__path__ = []  # type: ignore[attr-defined]
    edge_platform_module = ModuleType("cyberwave.edge.platform")
    edge_platform_module.USBIP_LAUNCHD_LABEL = "com.cyberwave.usbip"
    edge_platform_module.USBIP_PORT = 3240
    edge_platform_module.is_port_listening = lambda port, host="127.0.0.1", timeout=1: False
    edge_platform_module.is_usbip_server_running = lambda: False
    edge_module.platform = edge_platform_module

    cyberwave_module.config = config_module
    cyberwave_module.fingerprint = fingerprint_module
    cyberwave_module.edge = edge_module

    rich_module = ModuleType("rich")
    rich_console_module = ModuleType("rich.console")
    rich_prompt_module = ModuleType("rich.prompt")

    class _Console:
        def print(self, *_args, **_kwargs):
            return None

        def status(self, *_args, **_kwargs):
            class _Status:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, exc_type, exc, tb):
                    return False

            return _Status()

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
    cli_config_module.LEGACY_SYSTEM_CONFIG_DIR = Path("/tmp/nonexistent-cyberwave-legacy")
    cli_config_module.clean_subprocess_env = lambda: {}
    cli_config_module.get_api_url = lambda: "https://api.example.test"
    cli_config_module._resolve_sudo_user_home = lambda: None
    cli_config_module.chown_to_sudo_user = lambda *_paths: None

    credentials_module = ModuleType("cyberwave_cli.credentials")
    credentials_module.Credentials = object
    credentials_module.collect_runtime_env_overrides = lambda *args, **kwargs: {}
    credentials_module.load_credentials = lambda: None
    credentials_module.save_credentials = lambda *_args, **_kwargs: None

    monkeypatch.setitem(sys.modules, "cyberwave", cyberwave_module)
    monkeypatch.setitem(sys.modules, "cyberwave.config", config_module)
    monkeypatch.setitem(sys.modules, "cyberwave.edge", edge_module)
    monkeypatch.setitem(sys.modules, "cyberwave.edge.platform", edge_platform_module)
    monkeypatch.setitem(sys.modules, "cyberwave.fingerprint", fingerprint_module)
    monkeypatch.setitem(sys.modules, "rich", rich_module)
    monkeypatch.setitem(sys.modules, "rich.console", rich_console_module)
    monkeypatch.setitem(sys.modules, "rich.prompt", rich_prompt_module)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.auth", auth_module)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", cli_config_module)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", credentials_module)

    package_root = Path(__file__).resolve().parents[1] / "cyberwave_cli"
    cyberwave_cli_package = ModuleType("cyberwave_cli")
    cyberwave_cli_package.__path__ = [str(package_root)]  # type: ignore[attr-defined]
    sys.modules["cyberwave_cli"] = cyberwave_cli_package

    sys.modules.pop("cyberwave_cli.macos", None)
    sys.modules.pop("cyberwave_cli.core", None)

    spec = importlib.util.spec_from_file_location(
        "cyberwave_cli.core",
        package_root / "core.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cyberwave_cli.core"] = module
    spec.loader.exec_module(module)
    return module
