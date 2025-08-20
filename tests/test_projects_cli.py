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

    cyberwave_stub.Client = DummyClient
    sys.modules['cyberwave'] = cyberwave_stub

from typer.testing import CliRunner
from unittest.mock import AsyncMock

from cyberwave_cli.core import app as core_app
from cyberwave_cli.plugins.projects.app import app as projects_app
import cyberwave_cli.plugins.projects.app as projects_module

# Register projects subcommand
core_app.add_typer(projects_app, name="projects")
runner = CliRunner()


class DummyClient:
    def __init__(self, *args, **kwargs):
        self.login = AsyncMock()
        self.create_project = AsyncMock(return_value={"id": 1})
        self.get_projects = AsyncMock(return_value=[{"id": 1, "name": "Demo"}])


def test_create_command(monkeypatch):
    dummy = DummyClient()
    monkeypatch.setattr(projects_module, "Client", lambda: dummy)

    result = runner.invoke(
        core_app,
        ["projects", "create", "--workspace", "1", "--name", "Demo"],
        prog_name="cyberwave",
    )

    assert result.exit_code == 0
    dummy.login.assert_awaited_once()
    dummy.create_project.assert_awaited_once_with(workspace_id=1, name="Demo")


def test_list_command(monkeypatch):
    dummy = DummyClient()
    monkeypatch.setattr(projects_module, "Client", lambda: dummy)

    result = runner.invoke(
        core_app,
        ["projects", "list", "--workspace", "1"],
        prog_name="cyberwave",
    )

    assert result.exit_code == 0
    dummy.login.assert_awaited_once()
    dummy.get_projects.assert_awaited_once_with(workspace_id=1)
    assert "Demo" in result.stdout
