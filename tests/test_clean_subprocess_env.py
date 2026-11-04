"""Tests for the environment the frozen CLI hands to child processes."""

import sys

import pytest

from cyberwave_cli.config import clean_subprocess_env

FAKE_MEIPASS = "/tmp/_MEIfakedir"
HOST_BUNDLE = "/etc/ssl/certs/ca-certificates.crt"


@pytest.fixture(params=[None, "/opt/lib"], ids=["no-ld-orig", "ld-orig"])
def frozen(request, monkeypatch) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", FAKE_MEIPASS, raising=False)
    if request.param is None:
        monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    else:
        monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", request.param)


def test_a_ca_bundle_in_the_extraction_dir_is_dropped(frozen, monkeypatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", f"{FAKE_MEIPASS}/certifi/cacert.pem")

    assert "SSL_CERT_FILE" not in clean_subprocess_env()


def test_a_ca_bundle_outside_the_extraction_dir_is_passed_on(frozen, monkeypatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", HOST_BUNDLE)

    assert clean_subprocess_env()["SSL_CERT_FILE"] == HOST_BUNDLE
