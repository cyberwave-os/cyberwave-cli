"""Unit tests for ``cyberwave_cli.utils._resolve_mqtt_kwargs`` and friends.

These tests exercise the MQTT kwarg derivation that ultimately decides
which ``topic_prefix`` the SDK uses when the CLI publishes commands like
``cyberwave workflow sync``.

The historical bug they pin down: ``_resolve_mqtt_kwargs`` used to forward
``mqtt_host`` and ``mqtt_port`` but *not* ``topic_prefix``, which meant
the CLI published to the un-prefixed topic ``cyberwave/twin/.../command``
while edge-core (running with ``CYBERWAVE_ENVIRONMENT=dev``) subscribed
under ``devcyberwave/twin/.../command`` — every sync was silently dropped.
"""

from __future__ import annotations

import importlib

import click
import pytest

from cyberwave_cli.credentials import Credentials

utils_module = importlib.import_module("cyberwave_cli.utils")


# ---------------------------------------------------------------------------
# topic_prefix forwarding (Fix 1)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "environment, mqtt_host, expected_prefix",
    [
        # The exact scenario from the bug report: dev creds + dev broker
        # must produce topic_prefix="dev" so we hit devcyberwave/...
        ("dev", "dev.mqtt.cyberwave.com", "dev"),
        ("staging", "staging.mqtt.cyberwave.com", "staging"),
        # Production normalizes to an empty prefix (matches the SDK).
        ("production", "mqtt.cyberwave.com", ""),
        # Local dev — Docker Compose layout.
        ("local", "localhost", "local"),
    ],
)
def test_resolve_mqtt_kwargs_forwards_topic_prefix_per_environment(
    monkeypatch,
    environment: str,
    mqtt_host: str,
    expected_prefix: str,
) -> None:
    monkeypatch.delenv("CYBERWAVE_MQTT_TOPIC_PREFIX", raising=False)

    creds = Credentials(
        token="token-123",
        cyberwave_environment=environment,
        cyberwave_mqtt_host=mqtt_host,
    )
    base_url = (
        "http://localhost:8000"
        if environment == "local"
        else f"https://api{'-' + environment if environment != 'production' else ''}.cyberwave.com"
    )

    kwargs = utils_module._resolve_mqtt_kwargs(creds, base_url)

    assert kwargs.get("topic_prefix") == expected_prefix, (
        f"environment={environment!r} on host {mqtt_host!r} must produce "
        f"topic_prefix={expected_prefix!r}; got {kwargs.get('topic_prefix')!r}"
    )


def test_resolve_mqtt_kwargs_local_base_url_overrides_credentials_mqtt_host(
    monkeypatch,
) -> None:
    """Local ``base_url`` short-circuits to ``localhost:1883`` regardless of
    whatever stale broker host is sitting in credentials, and the prefix
    follows the same logic so the CLI talks to the local dev broker.
    """
    monkeypatch.delenv("CYBERWAVE_MQTT_TOPIC_PREFIX", raising=False)

    creds = Credentials(
        token="token-123",
        cyberwave_environment="local",
        cyberwave_mqtt_host="dev.mqtt.cyberwave.com",  # stale, must be ignored
    )

    kwargs = utils_module._resolve_mqtt_kwargs(creds, "http://localhost:8000")

    assert kwargs.get("mqtt_host") == "localhost"
    assert kwargs.get("mqtt_port") == 1883
    assert kwargs.get("topic_prefix") == "local"


def test_resolve_mqtt_kwargs_explicit_env_var_wins(monkeypatch) -> None:
    """``CYBERWAVE_MQTT_TOPIC_PREFIX`` is the documented escape hatch and
    must override whatever credentials say.

    We point at a custom (non-cyberwave) broker so the consistency guard
    stays out of the picture — the env-var-precedence rule is what's
    under test here, not the prefix/host validation.
    """
    monkeypatch.setenv("CYBERWAVE_MQTT_TOPIC_PREFIX", "custom-prefix")

    creds = Credentials(
        token="token-123",
        cyberwave_environment="dev",
        cyberwave_mqtt_host="mqtt.example.internal",
    )

    kwargs = utils_module._resolve_mqtt_kwargs(
        creds, "https://api.example.internal"
    )

    assert kwargs.get("topic_prefix") == "custom-prefix"


def test_resolve_mqtt_kwargs_falls_back_to_inferred_prefix_from_host(
    monkeypatch,
) -> None:
    """Old credentials saved before ``cyberwave_environment`` was persisted
    still produce the right prefix when the broker host is recognizable.
    """
    monkeypatch.delenv("CYBERWAVE_MQTT_TOPIC_PREFIX", raising=False)

    creds = Credentials(
        token="token-123",
        cyberwave_environment=None,
        cyberwave_mqtt_host="dev.mqtt.cyberwave.com",
    )

    kwargs = utils_module._resolve_mqtt_kwargs(
        creds, "https://api-dev.cyberwave.com"
    )

    assert kwargs.get("topic_prefix") == "dev"


def test_resolve_mqtt_kwargs_unknown_broker_skips_topic_prefix(
    monkeypatch,
) -> None:
    """Custom on-prem brokers shouldn't get a guessed prefix or trip the
    consistency guard — leave the kwarg unset and let the SDK decide.
    """
    monkeypatch.delenv("CYBERWAVE_MQTT_TOPIC_PREFIX", raising=False)

    creds = Credentials(
        token="token-123",
        cyberwave_environment=None,
        cyberwave_mqtt_host="mqtt.example.internal",
    )

    kwargs = utils_module._resolve_mqtt_kwargs(
        creds, "https://api.example.internal"
    )

    assert "topic_prefix" not in kwargs


# ---------------------------------------------------------------------------
# Consistency guard (Fix 2)
# ---------------------------------------------------------------------------


def test_resolve_mqtt_kwargs_raises_on_env_host_mismatch(monkeypatch) -> None:
    """Mismatched ``cyberwave_environment`` and broker host means published
    commands land on a topic edge-core never subscribed to.  The CLI must
    abort instead of silently lying on success.
    """
    monkeypatch.delenv("CYBERWAVE_MQTT_TOPIC_PREFIX", raising=False)

    creds = Credentials(
        token="token-123",
        cyberwave_environment="staging",  # says staging
        cyberwave_mqtt_host="dev.mqtt.cyberwave.com",  # but broker is dev
    )

    with pytest.raises(click.ClickException) as excinfo:
        utils_module._resolve_mqtt_kwargs(
            creds, "https://api-dev.cyberwave.com"
        )

    msg = str(excinfo.value.message)
    assert "topic prefix" in msg.lower()
    assert "dev.mqtt.cyberwave.com" in msg
    assert "staging" in msg
    assert "dev" in msg


def test_resolve_mqtt_kwargs_raises_when_prefix_empty_against_dev_broker(
    monkeypatch,
) -> None:
    """The exact bug from the field: credentials had no
    ``cyberwave_environment`` and the user expected the CLI to "just work".
    The fallback (Fix 1) now infers ``dev`` from the host, so this only
    triggers when somebody explicitly forces the prefix to empty.
    """
    monkeypatch.setenv("CYBERWAVE_MQTT_TOPIC_PREFIX", " ")  # blank → ignored
    monkeypatch.setenv("CYBERWAVE_MQTT_TOPIC_PREFIX", "")  # empty → ignored

    creds = Credentials(
        token="token-123",
        cyberwave_environment="production",  # forces prefix=""
        cyberwave_mqtt_host="dev.mqtt.cyberwave.com",
    )

    with pytest.raises(click.ClickException) as excinfo:
        utils_module._resolve_mqtt_kwargs(
            creds, "https://api-dev.cyberwave.com"
        )

    assert "dev" in str(excinfo.value.message)


def test_resolve_mqtt_kwargs_explicit_prefix_override_still_validated(
    monkeypatch,
) -> None:
    """Even when the user sets ``CYBERWAVE_MQTT_TOPIC_PREFIX`` explicitly,
    we still validate consistency — the override is for *renaming* the
    prefix, not for silently shipping into a black hole.
    """
    monkeypatch.setenv("CYBERWAVE_MQTT_TOPIC_PREFIX", "production")

    creds = Credentials(
        token="token-123",
        cyberwave_environment="dev",
        cyberwave_mqtt_host="dev.mqtt.cyberwave.com",
    )

    with pytest.raises(click.ClickException):
        utils_module._resolve_mqtt_kwargs(
            creds, "https://api-dev.cyberwave.com"
        )


def test_resolve_mqtt_kwargs_unknown_host_skips_consistency_check(
    monkeypatch,
) -> None:
    """We can't infer an environment from a custom broker host, so the
    consistency guard stays silent and trusts whatever prefix the user
    provided.
    """
    monkeypatch.setenv("CYBERWAVE_MQTT_TOPIC_PREFIX", "weird-prefix")

    creds = Credentials(
        token="token-123",
        cyberwave_environment="dev",
        cyberwave_mqtt_host="mqtt.example.internal",
    )

    kwargs = utils_module._resolve_mqtt_kwargs(
        creds, "https://api.example.internal"
    )

    assert kwargs.get("topic_prefix") == "weird-prefix"


# ---------------------------------------------------------------------------
# get_sdk_client integration — make sure the kwargs actually reach Cyberwave()
# ---------------------------------------------------------------------------


def test_get_sdk_client_passes_topic_prefix_to_cyberwave_constructor(
    monkeypatch,
) -> None:
    """End-to-end check: a credentials.json with ``cyberwave_environment=dev``
    must result in ``Cyberwave(..., topic_prefix='dev', ...)``.

    This is the regression test for the original bug — without Fix 1 the
    SDK would never see ``topic_prefix`` and would default to "" (because
    the CLI process doesn't have ``CYBERWAVE_ENVIRONMENT`` exported).
    """
    monkeypatch.delenv("CYBERWAVE_MQTT_TOPIC_PREFIX", raising=False)
    monkeypatch.delenv("CYBERWAVE_ENVIRONMENT", raising=False)

    captured: dict = {}

    class _FakeCyberwave:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import cyberwave as cyberwave_pkg

    monkeypatch.setattr(cyberwave_pkg, "Cyberwave", _FakeCyberwave)

    monkeypatch.setattr(
        utils_module,
        "load_credentials",
        lambda: Credentials(
            token="token-123",
            cyberwave_environment="dev",
            cyberwave_base_url="https://api-dev.cyberwave.com",
            cyberwave_mqtt_host="dev.mqtt.cyberwave.com",
            cyberwave_mqtt_port="8883",
        ),
    )

    client = utils_module.get_sdk_client()

    assert client is not None
    assert captured.get("base_url") == "https://api-dev.cyberwave.com"
    assert captured.get("token") == "token-123"
    assert captured.get("mqtt_host") == "dev.mqtt.cyberwave.com"
    assert captured.get("topic_prefix") == "dev", (
        "Regression: get_sdk_client() must forward topic_prefix='dev' so "
        "MQTT publishes land on devcyberwave/... (the topic edge-core "
        "actually subscribes to)."
    )
