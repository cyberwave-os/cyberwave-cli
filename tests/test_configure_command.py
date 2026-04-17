"""Tests for the cyberwave configure command."""

import importlib

from click.testing import CliRunner

import pytest

_configure_module = importlib.import_module("cyberwave_cli.commands.configure")


def test_configure_saves_registry_tokens_without_overwriting_existing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _configure_module.Credentials(
        token="api-token",
        email="user@example.com",
        workspace_uuid="ws-1",
        workspace_name="Main",
    )
    saved: dict[str, object] = {}

    monkeypatch.setattr(_configure_module, "load_credentials", lambda: existing)
    monkeypatch.setattr(_configure_module, "get_api_url", lambda: "https://api.example.com")
    monkeypatch.setattr(_configure_module, "collect_runtime_env_overrides", lambda **_kwargs: {})
    monkeypatch.setattr(
        _configure_module,
        "save_credentials",
        lambda credentials: saved.setdefault("credentials", credentials),
    )

    runner = CliRunner()
    result = runner.invoke(
        _configure_module.configure,
        [
            "--internal-deb-read-token",
            "deb-secret-token",
            "--internal-python-read-token",
            "python-secret-token",
        ],
    )

    assert result.exit_code == 0
    credentials = saved["credentials"]
    assert credentials.token == "api-token"
    assert credentials.email == "user@example.com"
    assert credentials.internal_deb_read_token == "deb-secret-token"
    assert credentials.internal_python_read_token == "python-secret-token"


def test_configure_show_redacts_registry_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    creds = _configure_module.Credentials(
        token="api-token-abcdefghijklmnopqrstuvwxyz",
        internal_deb_read_token="deb-secret-token",
        internal_python_read_token="python-secret-token",
    )
    printed: list[str] = []

    monkeypatch.setattr(_configure_module, "load_credentials", lambda: creds)
    monkeypatch.setattr(_configure_module, "get_api_url", lambda: "https://api.example.com")
    monkeypatch.setattr(
        _configure_module.console,
        "print",
        lambda message="", *args, **kwargs: printed.append(str(message)),
    )

    runner = CliRunner()
    result = runner.invoke(_configure_module.configure, ["--show"])

    assert result.exit_code == 0
    output = "\n".join(printed)
    assert "deb-secret-token" not in output
    assert "python-secret-token" not in output
    assert "Internal deb token:" in output
    assert "Internal python token:" in output
