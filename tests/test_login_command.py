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
    monkeypatch.setattr(_login_module, "_validate_stored_token", lambda token, base_url: True)

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


def test_login_with_token_persists_mqtt_runtime_overrides(
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
        lambda: {
            "CYBERWAVE_BASE_URL": "http://localhost:8000",
            "CYBERWAVE_MQTT_HOST": "localhost",
            "CYBERWAVE_MQTT_PORT": "1883",
        },
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
    assert credentials.cyberwave_base_url == "http://localhost:8000"
    assert credentials.cyberwave_mqtt_host == "localhost"
    assert credentials.cyberwave_mqtt_port == "1883"


def _token_login_against(
    monkeypatch: pytest.MonkeyPatch, stored_base_url: str | None
) -> tuple[list[str | None], MagicMock]:
    """Run ``login --token`` over stored credentials; return the URLs AuthClient got."""
    existing = _login_module.Credentials(token="old-token", cyberwave_base_url=stored_base_url)
    monkeypatch.setattr(_login_module, "load_credentials", lambda: existing)
    monkeypatch.setattr(_login_module, "save_credentials", lambda _credentials: None)
    monkeypatch.setattr(_login_module, "collect_runtime_env_overrides", lambda: {})

    mock_cm = MagicMock()
    mock_client = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    mock_client.get_api_token_context.return_value = APITokenContext(
        email="user@example.com",
        workspace_uuid="ws-1",
        workspace_name="Main",
    )
    base_urls: list[str | None] = []

    def _auth_client(base_url: str | None = None) -> MagicMock:
        base_urls.append(base_url)
        return mock_cm

    monkeypatch.setattr(_login_module, "AuthClient", _auth_client)
    return base_urls, mock_client


def test_login_with_token_uses_stored_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A device paired against dev must not re-login against production."""
    monkeypatch.delenv("CYBERWAVE_BASE_URL", raising=False)
    base_urls, _ = _token_login_against(monkeypatch, "https://api-dev.example.com")

    result = CliRunner().invoke(_login_module.login, ["--token", "token-123"])

    assert result.exit_code == 0
    assert base_urls == ["https://api-dev.example.com"]


def test_login_env_base_url_beats_stored_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYBERWAVE_BASE_URL", "https://api-staging.example.com")
    base_urls, _ = _token_login_against(monkeypatch, "https://api-dev.example.com")

    result = CliRunner().invoke(_login_module.login, ["--token", "token-123"])

    assert result.exit_code == 0
    assert base_urls == ["https://api-staging.example.com"]


@pytest.mark.parametrize(
    ("stored_url", "shown"),
    [("https://api-dev.example.com", ["Backend: https://api-dev.example.com"]), (None, [])],
)
def test_login_success_names_a_backend_other_than_production(
    monkeypatch: pytest.MonkeyPatch, stored_url: str | None, shown: list[str]
) -> None:
    monkeypatch.delenv("CYBERWAVE_BASE_URL", raising=False)
    _token_login_against(monkeypatch, stored_url)

    result = CliRunner().invoke(_login_module.login, ["--token", "token-123"])

    assert result.exit_code == 0
    assert [line.strip() for line in result.output.splitlines() if "Backend:" in line] == shown


def test_login_failure_names_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CYBERWAVE_BASE_URL", raising=False)
    _, mock_client = _token_login_against(monkeypatch, "https://api-dev.example.com")
    mock_client.get_api_token_context.side_effect = AuthenticationError("Invalid or expired token")

    result = CliRunner().invoke(_login_module.login, ["--token", "bad-token"])

    assert result.exit_code != 0
    assert "https://api-dev.example.com" in result.output


@pytest.mark.parametrize(
    ("env_url", "probed"),
    [
        (None, ["https://api-dev.example.com"]),
        (
            "https://api-staging.example.com",
            ["https://api-dev.example.com", "https://api-staging.example.com"],
        ),
    ],
)
def test_stored_token_is_not_rechecked_on_production(
    monkeypatch: pytest.MonkeyPatch, env_url: str | None, probed: list[str]
) -> None:
    """A dev token that fails its check would be reported invalid by production."""
    if env_url is None:
        monkeypatch.delenv("CYBERWAVE_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("CYBERWAVE_BASE_URL", env_url)
    existing = _login_module.Credentials(
        token="old-token", cyberwave_base_url="https://api-dev.example.com"
    )
    monkeypatch.setattr(_login_module, "load_credentials", lambda: existing)
    probed_urls: list[str] = []

    def _validate(token: str, base_url: str) -> bool:
        probed_urls.append(base_url)
        return False

    monkeypatch.setattr(_login_module, "_validate_stored_token", _validate)
    mock_cm = MagicMock()
    mock_cm.__enter__.return_value.login.side_effect = AuthenticationError(
        "Invalid email or password"
    )
    monkeypatch.setattr(_login_module, "AuthClient", lambda *args, **kwargs: mock_cm)

    result = CliRunner().invoke(
        _login_module.login, ["--email", "a@example.com", "--password", "pw"]
    )

    assert result.exit_code == 1
    assert probed_urls == probed
