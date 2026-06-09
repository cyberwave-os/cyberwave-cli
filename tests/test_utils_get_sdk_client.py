from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cyberwave_cli.credentials import Credentials

utils_module = importlib.import_module("cyberwave_cli.utils")


def test_get_sdk_client_passes_logged_in_workspace(monkeypatch) -> None:
    creds = Credentials(
        token="token-123",
        workspace_uuid="ws-skydio",
        workspace_name="Skydio",
        cyberwave_base_url="http://localhost:8000",
    )
    captured: dict = {}

    class _FakeCyberwave:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(utils_module, "load_credentials", lambda: creds)
    monkeypatch.setattr(utils_module, "resolve_api_url", lambda *_a, **_k: "http://localhost:8000")
    monkeypatch.setattr(utils_module, "_resolve_mqtt_kwargs", lambda *_a, **_k: {})

    with patch.dict("sys.modules", {"cyberwave": SimpleNamespace(Cyberwave=_FakeCyberwave)}):
        client = utils_module.get_sdk_client()

    assert client is not None
    assert captured["workspace_id"] == "ws-skydio"
    assert captured["token"] == "token-123"


def test_get_sdk_client_omits_workspace_when_credentials_have_none(monkeypatch) -> None:
    creds = Credentials(token="token-123")
    captured: dict = {}

    class _FakeCyberwave:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(utils_module, "load_credentials", lambda: creds)
    monkeypatch.setattr(utils_module, "resolve_api_url", lambda *_a, **_k: "http://localhost:8000")
    monkeypatch.setattr(utils_module, "_resolve_mqtt_kwargs", lambda *_a, **_k: {})

    with patch.dict("sys.modules", {"cyberwave": SimpleNamespace(Cyberwave=_FakeCyberwave)}):
        utils_module.get_sdk_client()

    assert "workspace_id" not in captured
