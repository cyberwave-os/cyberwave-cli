"""Tests for the cyberwave login command."""

import importlib
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from cyberwave_cli.auth import APITokenContext, AuthenticationError

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


def test_login_with_token_saves_enriched_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_login_module, "load_credentials", lambda: None)

    saved: dict[str, object] = {}

    def _save_credentials(credentials: object) -> None:
        saved["credentials"] = credentials

    monkeypatch.setattr(_login_module, "save_credentials", _save_credentials)
    monkeypatch.setattr(
        _login_module,
        "collect_runtime_env_overrides",
        lambda: {"CYBERWAVE_BASE_URL": "https://api.example.com"},
    )

    mock_cm = MagicMock()
    mock_client = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    mock_client.get_api_token_context.return_value = APITokenContext(
        email="user@example.com",
        workspace_uuid="ws-1",
        workspace_name="Main",
    )
    monkeypatch.setattr(_login_module, "AuthClient", lambda *args, **kwargs: mock_cm)

    runner = CliRunner()
    result = runner.invoke(_login_module.login, ["--token", "token-123"])

    assert result.exit_code == 0
    credentials = saved["credentials"]
    assert credentials.token == "token-123"
    assert credentials.email == "user@example.com"
    assert credentials.workspace_uuid == "ws-1"
    assert credentials.workspace_name == "Main"
    assert credentials.cyberwave_base_url == "https://api.example.com"
    mock_client.get_workspaces.assert_not_called()


def test_login_with_invalid_token_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_login_module, "load_credentials", lambda: None)

    mock_cm = MagicMock()
    mock_client = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    mock_client.get_api_token_context.side_effect = AuthenticationError("Invalid or expired token")
    monkeypatch.setattr(_login_module, "AuthClient", lambda *args, **kwargs: mock_cm)

    runner = CliRunner()
    result = runner.invoke(_login_module.login, ["--token", "bad-token"])

    assert result.exit_code != 0
    assert "login failed" in result.output.lower()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--token", "token-123", "--email", "user@example.com"], "mutually exclusive"),
        (["--token", "token-123", "--password", "secret"], "mutually exclusive"),
    ],
)
def test_login_rejects_token_with_other_auth_inputs(
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    message: str,
) -> None:
    monkeypatch.setattr(_login_module, "load_credentials", lambda: None)

    runner = CliRunner()
    result = runner.invoke(_login_module.login, args)

    assert result.exit_code != 0
    assert message in result.output.lower()


@pytest.mark.parametrize("raw_token", ["", "   "])
def test_login_rejects_empty_token(
    monkeypatch: pytest.MonkeyPatch,
    raw_token: str,
) -> None:
    monkeypatch.setattr(_login_module, "load_credentials", lambda: None)

    runner = CliRunner()
    result = runner.invoke(_login_module.login, ["--token", raw_token])

    assert result.exit_code != 0
    assert "token" in result.output.lower()


def test_login_with_token_and_unavailable_workspace_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_login_module, "load_credentials", lambda: None)

    mock_cm = MagicMock()
    mock_client = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    mock_client.get_api_token_context.side_effect = AuthenticationError(
        "API token workspace context is unavailable"
    )
    monkeypatch.setattr(_login_module, "AuthClient", lambda *args, **kwargs: mock_cm)

    runner = CliRunner()
    result = runner.invoke(_login_module.login, ["--token", "token-123"])

    assert result.exit_code != 0
    assert "login failed" in result.output.lower()
    assert "api token workspace context is unavailable" in result.output.lower()


def test_login_with_token_bypasses_already_logged_in_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _login_module.Credentials(
        token="old-token",
        email="old@example.com",
        workspace_uuid="old-ws",
        workspace_name="Old Workspace",
    )
    monkeypatch.setattr(_login_module, "load_credentials", lambda: existing)
    monkeypatch.setattr(_login_module, "_validate_stored_token", lambda token: True)

    saved: dict[str, object] = {}

    def _save_credentials(credentials: object) -> None:
        saved["credentials"] = credentials

    monkeypatch.setattr(_login_module, "save_credentials", _save_credentials)
    monkeypatch.setattr(_login_module, "collect_runtime_env_overrides", lambda: {})

    mock_cm = MagicMock()
    mock_client = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    mock_client.get_api_token_context.return_value = APITokenContext(
        email="new@example.com",
        workspace_uuid="ws-new",
        workspace_name="New Workspace",
    )
    monkeypatch.setattr(_login_module, "AuthClient", lambda *args, **kwargs: mock_cm)

    runner = CliRunner()
    result = runner.invoke(_login_module.login, ["--token", "new-token"])

    assert result.exit_code == 0
    assert saved["credentials"].token == "new-token"
    assert "already logged in" not in result.output.lower()


def test_login_with_token_noninteractive_uses_first_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_login_module, "load_credentials", lambda: None)

    saved: dict[str, object] = {}

    def _save_credentials(credentials: object) -> None:
        saved["credentials"] = credentials

    monkeypatch.setattr(_login_module, "save_credentials", _save_credentials)
    monkeypatch.setattr(_login_module, "collect_runtime_env_overrides", lambda: {})

    mock_cm = MagicMock()
    mock_client = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    mock_client.get_api_token_context.return_value = APITokenContext(
        email="user@example.com",
        workspace_uuid="ws-1",
        workspace_name="First",
    )
    monkeypatch.setattr(_login_module, "AuthClient", lambda *args, **kwargs: mock_cm)
    monkeypatch.setattr(_login_module.sys.stdin, "isatty", lambda: False)

    runner = CliRunner()
    result = runner.invoke(_login_module.login, ["--token", "token-123"])

    assert result.exit_code == 0
    assert saved["credentials"].workspace_uuid == "ws-1"
    mock_client.get_workspaces.assert_not_called()


def test_login_with_token_uses_api_token_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_login_module, "load_credentials", lambda: None)
    monkeypatch.setattr(_login_module, "collect_runtime_env_overrides", lambda: {})
    monkeypatch.setattr(_login_module, "save_credentials", lambda _credentials: None)

    mock_cm = MagicMock()
    mock_client = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    mock_client.get_api_token_context.return_value = APITokenContext(
        email="user@example.com",
        workspace_uuid="ws-1",
        workspace_name="Main",
    )
    monkeypatch.setattr(_login_module, "AuthClient", lambda *args, **kwargs: mock_cm)

    runner = CliRunner()
    result = runner.invoke(_login_module.login, ["--token", "token-123"])

    assert result.exit_code == 0
    mock_client.get_api_token_context.assert_called_once_with("token-123")
