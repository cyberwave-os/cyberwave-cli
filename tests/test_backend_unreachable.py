import errno
import importlib
import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner
from cyberwave.config import DEFAULT_BASE_URL

from cyberwave_cli.auth import AuthenticationError
from cyberwave_cli.url_utils import backend_line, backend_unreachable_message, unreachable_reason
from tests._core_module_loader import load_core_module

_login_module = importlib.import_module("cyberwave_cli.commands.login")

_SDK_NO_ROUTE = RuntimeError(
    "Failed to list workspaces: HTTPConnectionPool(host='192.168.88.138', port=8000): "
    "Max retries exceeded with url: /api/v1/users/workspaces (Caused by NewConnectionError("
    "\"HTTPConnection(host='192.168.88.138', port=8000): Failed to establish a new "
    f"connection: [Errno {errno.EHOSTUNREACH}] No route to host\"))"
)


def _chained(outer: Exception, inner: BaseException) -> Exception:
    outer.__cause__ = inner
    return outer


@pytest.mark.parametrize(
    ("exc", "reason"),
    [
        (_SDK_NO_ROUTE, "no route to host"),
        (_chained(RuntimeError("boom"), ConnectionRefusedError(errno.ECONNREFUSED, "x")),
         "connection refused"),
        (_chained(RuntimeError("boom"), socket.gaierror(-2, "Name or service not known")),
         "host name not found"),
        (AuthenticationError("Connection error: timed out"), "timed out"),
        (AuthenticationError("Connection error: [Errno 113] No route to host"),
         "no route to host"),
    ],
)
def test_unreachable_reason_names_the_network_failure(exc, reason):
    assert unreachable_reason(exc) == reason


@pytest.mark.parametrize(
    "exc",
    [RuntimeError("401 Unauthorized"), AuthenticationError("Invalid email or password")],
)
def test_unreachable_reason_ignores_a_backend_that_answered(exc):
    assert unreachable_reason(exc) is None


def test_unreachable_message_hides_userinfo():
    message = backend_unreachable_message("http://user:secret@host:8000", "timed out")
    assert "secret" not in message
    assert "Can't reach the Cyberwave backend" in message


@pytest.mark.parametrize("url", [None, "", DEFAULT_BASE_URL, DEFAULT_BASE_URL + "/"])
def test_backend_line_leaves_production_unmentioned(url):
    assert backend_line(url) is None


def test_backend_line_names_another_backend_without_userinfo():
    line = backend_line("http://user:secret@192.168.88.93:8000")
    assert "Backend: http://***@192.168.88.93:8000" in line
    assert "secret" not in line


class _Status:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _core_with_offline_backend(monkeypatch, *, offline_urls, saved_url, env_url):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "get_api_url", lambda: env_url)
    monkeypatch.setattr(
        core,
        "collect_runtime_env_overrides",
        lambda *a, **kw: {"CYBERWAVE_BASE_URL": env_url},
    )
    printed: list[str] = []
    monkeypatch.setattr(
        core,
        "console",
        SimpleNamespace(
            print=lambda *args, **kwargs: printed.append(str(args[0])),
            status=lambda *args, **kwargs: _Status(),
        ),
    )
    monkeypatch.setattr(
        core,
        "load_credentials",
        lambda: SimpleNamespace(
            token="cw_saved",
            email="op@example.test",
            created_at=None,
            workspace_uuid="ws-1",
            workspace_name="Main",
            cyberwave_base_url=saved_url,
        ),
    )
    monkeypatch.setattr(core, "save_credentials", lambda creds: None)
    monkeypatch.setattr(core, "Credentials", lambda **kw: SimpleNamespace(**kw))

    def _client(token, base_url=None):
        url = base_url or core.get_api_url()

        def _list():
            if url in offline_urls:
                raise _SDK_NO_ROUTE
            return [SimpleNamespace(uuid="ws-1", name="Main", slug="main")]

        return SimpleNamespace(workspaces=SimpleNamespace(list=_list))

    monkeypatch.setattr(core, "_get_sdk_client", _client)
    monkeypatch.setattr(
        core.Prompt,
        "ask",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not prompt")),
    )
    return core, printed


def test_offline_backend_stops_the_install_without_prompting(monkeypatch):
    url = "http://192.168.88.138:8000"
    monkeypatch.setenv("CYBERWAVE_API_KEY", "cw_env")
    core, printed = _core_with_offline_backend(
        monkeypatch, offline_urls={url}, saved_url=url, env_url=url
    )
    attempted = []
    monkeypatch.setattr(
        core, "_authenticate_with_api_token", lambda *a, **kw: attempted.append(kw["source"])
    )

    assert core._ensure_credentials(skip_confirm=True) is False

    assert attempted == [], "the env token targets the same dead backend"
    assert sum("Can't reach the Cyberwave backend" in line for line in printed) == 1
    assert "no route to host" in "".join(printed)
    assert not any("invalid" in line or "rejected" in line for line in printed)


def test_stale_saved_backend_is_replaced_by_the_env_backend(monkeypatch):
    """The saved token is checked on CYBERWAVE_BASE_URL too, so it is never saved
    next to a backend it was not checked on."""
    old, new = "http://192.168.88.138:8000", "http://192.168.88.93:8000"
    monkeypatch.setenv("CYBERWAVE_API_KEY", "cw_env")
    core, printed = _core_with_offline_backend(
        monkeypatch, offline_urls={old}, saved_url=old, env_url=new
    )
    attempted = []
    monkeypatch.setattr(
        core, "_authenticate_with_api_token", lambda *a, **kw: attempted.append(kw["source"])
    )
    saved = []
    monkeypatch.setattr(core, "save_credentials", saved.append)

    assert core._ensure_credentials(skip_confirm=True) is True
    assert attempted == [], "the saved token already works on the env backend"
    assert [(c.token, c.cyberwave_base_url) for c in saved] == [("cw_saved", new)]
    assert f"[dim]Backend: {new}[/dim]" in printed
    assert not any("Can't reach" in line for line in printed)


def test_login_stops_at_once_when_the_backend_is_unreachable(monkeypatch):
    monkeypatch.setattr(_login_module, "load_credentials", lambda: None)
    mock_cm = MagicMock()
    mock_client = MagicMock()
    mock_cm.__enter__.return_value = mock_client
    mock_cm.__exit__.return_value = None
    mock_client.login.side_effect = AuthenticationError(
        "Connection error: [Errno 113] No route to host"
    )
    monkeypatch.setattr(_login_module, "AuthClient", lambda *args, **kwargs: mock_cm)

    result = CliRunner().invoke(
        _login_module.login, ["--email", "a@example.com", "--password", "pw"]
    )

    assert result.exit_code == 1
    assert "Can't reach the Cyberwave backend" in result.output
    assert "Aborted" not in result.output
    mock_client.login.assert_called_once()
