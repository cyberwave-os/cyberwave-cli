"""Tests for the cyberwave login command."""

import importlib
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from cyberwave_cli.auth import AuthenticationError

# `cyberwave_cli.commands.login` is the Click command on the package namespace; the
# implementation module must be loaded explicitly for monkeypatching.
_login_module = importlib.import_module("cyberwave_cli.commands.login")


def test_login_invalid_credentials_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wrong password must not exit 0 (regression: loop range vs MAX_ATTEMPTS mismatch)."""
    monkeypatch.setattr(_login_module, "load_credentials", lambda: None)

    mock_cm = MagicMock()
    mock_client = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    mock_client.login.side_effect = AuthenticationError(
        "Unable to log in with provided credentials."
    )

    monkeypatch.setattr(_login_module, "AuthClient", lambda *args, **kwargs: mock_cm)

    runner = CliRunner()
    result = runner.invoke(
        _login_module.login,
        ["--email", "wrong@example.com", "--password", "wrongpassword"],
    )

    assert result.exit_code != 0, "login with invalid credentials must fail"
    mock_client.login.assert_called_once()
