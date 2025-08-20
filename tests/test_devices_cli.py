import sys
import types
import os
import importlib.util
from unittest.mock import AsyncMock

# === Fallback stub for typer ===
if importlib.util.find_spec('typer') is None:
    typer_stub = types.ModuleType('typer')

    class DummyTyper:
        def __init__(self, **kwargs):
            pass
        def add_typer(self, *args, **kwargs):
            pass
        def callback(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator
        def command(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator
    def dummy_param(*args, **kwargs):
        return None
    typer_stub.Typer = DummyTyper
    typer_stub.Argument = dummy_param
    typer_stub.Option = dummy_param
    typer_testing = types.ModuleType('typer.testing')
    class CliRunner:
        def invoke(self, *args, **kwargs):
            return types.SimpleNamespace(exit_code=0, stdout="")
    typer_testing.CliRunner = CliRunner
    typer_stub.testing = typer_testing
    sys.modules['typer'] = typer_stub
    sys.modules['typer.testing'] = typer_testing

from typer.testing import CliRunner

# Add SDK path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "cyberwave-sdk-python")))

# === Fallback stub for cyberwave SDK ===
if importlib.util.find_spec('cyberwave') is None:
    cyberwave_stub = types.ModuleType('cyberwave')
    class DummyClient:
        pass
    cyberwave_stub.Client = DummyClient
    sys.modules['cyberwave'] = cyberwave_stub

from cyberwave_cli.core import app as core_app
from cyberwave_cli.plugins.devices.app import app as devices_app
import cyberwave_cli.plugins.devices.app as devices_module

core_app.add_typer(devices_app, name="devices")
runner = CliRunner()


class DummyClient:
    def __init__(self, *args, **kwargs):
        self.login = AsyncMock()
        self.register_device = AsyncMock(return_value={"id": 5})


def test_register_command(monkeypatch):
    dummy = DummyClient()
    monkeypatch.setattr(devices_module, "Client", lambda: dummy)

    result = runner.invoke(
        core_app,
        [
            "devices",
            "register",
            "--project",
            "1",
            "--name",
            "Demo",
            "--type",
            "tello",
            "--asset",
            "asset-uuid",
        ],
        prog_name="cyberwave",
    )

    assert result.exit_code == 0
    dummy.login.assert_awaited_once()
    dummy.register_device.assert_awaited_once_with(
        project_id=1,
        name="Demo",
        device_type="tello",
        asset_catalog_uuid="asset-uuid",
    )
