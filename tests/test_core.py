import sys
import types
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

from typer.testing import CliRunner
from importlib.metadata import version as pkg_version

# Typer app entry point
from cyberwave_cli.core import app

runner = CliRunner()

def test_version_command():
    """Test the 'cyberwave version' command."""
    result = runner.invoke(app, ["version"], prog_name="cyberwave")
    assert result.exit_code == 0, f"CLI exited with code {result.exit_code}: {result.stdout}"
    assert "CyberWave CLI version:" in result.stdout
    cli_version = pkg_version("cyberwave-cli")
    assert cli_version in result.stdout

def test_help_command():
    """Test the main help output."""
    result = runner.invoke(app, ["--help"], prog_name="cyberwave")
    assert result.exit_code == 0
    assert "Usage: cyberwave [OPTIONS] COMMAND [ARGS]..." in result.stdout
    assert "CyberWave Command-Line Interface" in result.stdout
    assert "version" in result.stdout

def test_invalid_command():
    """Test invoking a command that doesn't exist."""
    result = runner.invoke(app, ["nonexistent-command-foo-bar"], prog_name="cyberwave")
    assert result.exit_code != 0
    output = result.output or result.stdout or ""
    assert "Error" in output or "No such command" in output


def test_plugins_command():
    """Ensure the plugins command lists built-in plugins."""
    result = runner.invoke(app, ["plugins-cmd"], prog_name="cyberwave")
    assert result.exit_code == 0
    assert "auth" in result.stdout
