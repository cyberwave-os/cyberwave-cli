import sys
import types
import os
import importlib.util
import pytest

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

# === Add SDK path ===
sys.path.append(
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "cyberwave-sdk-python")
    )
)

# === Fallback stub for cyberwave SDK ===
if importlib.util.find_spec('cyberwave') is None:
    cyberwave_stub = types.ModuleType('cyberwave')

    class DummyClient:
        pass

    class DummyMesh:
        def __init__(self, *args, **kwargs):
            pass

    cyberwave_stub.Client = DummyClient
    cyberwave_stub.Mesh = DummyMesh
    sys.modules['cyberwave'] = cyberwave_stub

from typer.testing import CliRunner
from unittest.mock import AsyncMock
from importlib.metadata import version as pkg_version

from cyberwave_cli.core import app as core_app
from cyberwave_cli.plugins.assets.app import app as assets_app
import cyberwave_cli.plugins.assets.app as assets_module

# Register assets subcommand
core_app.add_typer(assets_app, name="assets")
runner = CliRunner()

### === DummyClient for CLI testing ===
class DummyClient:
    def __init__(self, *args, **kwargs):
        self.login = AsyncMock()
        self.create_asset_definition = AsyncMock(return_value={"id": 1})
        self.upload_mesh = AsyncMock(return_value={"id": 2})
        self.add_geometry_to_asset_definition = AsyncMock(return_value={"id": 3})

### === Tests ===
def test_upload_command(monkeypatch):
    dummy = DummyClient()
    monkeypatch.setattr(assets_module, "Client", lambda: dummy)

    mesh_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "cyberwave-sdk-python", "dummy_mesh.glb")
    )
    result = runner.invoke(
        core_app,
        [
            "assets",
            "upload",
            mesh_path,
            "--name", "Test Asset",
            "--workspace", "1",
            "--project", "2",
        ],
        prog_name="cyberwave",
    )

    assert result.exit_code == 0
    dummy.login.assert_awaited_once()
    dummy.create_asset_definition.assert_awaited_once()
    dummy.upload_mesh.assert_awaited_once()
    dummy.add_geometry_to_asset_definition.assert_awaited_once()

def test_version_command():
    result = runner.invoke(core_app, ["version"], prog_name="cyberwave")
    assert result.exit_code == 0
    assert "CyberWave CLI version:" in result.stdout
    cli_version = pkg_version("cyberwave-cli")
    assert cli_version in result.stdout

def test_help_command():
    result = runner.invoke(core_app, ["--help"], prog_name="cyberwave")
    assert result.exit_code == 0
    assert "Usage: cyberwave [OPTIONS] COMMAND [ARGS]..." in result.stdout
    assert "CyberWave Command-Line Interface" in result.stdout
    assert "version" in result.stdout

def test_invalid_command():
    result = runner.invoke(core_app, ["nonexistent-command-foo-bar"], prog_name="cyberwave")
    assert result.exit_code != 0
    output = result.output or result.stdout or ""
    assert "Error" in output or "No such command" in output
