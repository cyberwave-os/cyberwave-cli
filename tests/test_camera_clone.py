import importlib
import subprocess
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

camera = importlib.import_module("cyberwave_cli.commands.camera")


@pytest.mark.parametrize("outcome", ["success", "denied", "timeout"])
def test_camera_clone_is_noninteractive_and_bounded(monkeypatch, tmp_path, outcome):
    monkeypatch.setattr(camera, "load_credentials", lambda: SimpleNamespace(token="test"))
    monkeypatch.setattr(camera, "get_sdk_client", lambda token: object())
    monkeypatch.setattr(camera, "get_api_url", lambda: "http://example.test")
    monkeypatch.setattr(camera, "check_git_installed", lambda: True)
    monkeypatch.setattr(camera, "write_env_file", lambda *a, **kw: True)

    def run(command, **kwargs):
        assert command[:2] == ["git", "clone"]
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert kwargs["env"]["GCM_INTERACTIVE"] == "Never"
        assert kwargs["timeout"] == 60
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, 60)
        return subprocess.CompletedProcess(command, 0 if outcome == "success" else 128,
                                           "", "repository access denied")

    monkeypatch.setattr(camera.subprocess, "run", run)
    result = CliRunner().invoke(
        camera.camera, [str(tmp_path / "camera"), "-e", "env", "-t", "twin", "-y"]
    )
    assert result.exit_code == (0 if outcome == "success" else 1), result.output
    if outcome == "timeout":
        assert "timed out after 60 seconds" in result.output
    elif outcome == "denied":
        assert "Configure Git credentials" in result.output
