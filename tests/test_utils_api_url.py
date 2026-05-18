from __future__ import annotations

import importlib

from cyberwave_cli.credentials import Credentials

utils_module = importlib.import_module("cyberwave_cli.utils")


def test_resolve_api_url_uses_stored_credential_base_url(monkeypatch) -> None:
    creds = Credentials(
        token="token-123",
        cyberwave_base_url="http://localhost:8000",
    )

    monkeypatch.delenv("CYBERWAVE_BASE_URL", raising=False)
    monkeypatch.setattr(utils_module, "get_api_url", lambda: "https://api.cyberwave.com")

    assert utils_module.resolve_api_url(None, creds) == "http://localhost:8000"


def test_resolve_api_url_prefers_explicit_override(monkeypatch) -> None:
    creds = Credentials(
        token="token-123",
        cyberwave_base_url="http://localhost:8000",
    )

    monkeypatch.setenv("CYBERWAVE_BASE_URL", "https://env.example.com")

    assert (
        utils_module.resolve_api_url("https://flag.example.com", creds)
        == "https://flag.example.com"
    )
