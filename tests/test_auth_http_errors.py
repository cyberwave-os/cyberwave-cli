"""Tests for HTTP failure reporting in :mod:`cyberwave_cli.auth`.

A bare ``HTTP error: 403`` is not actionable — from a CI log it is
indistinguishable between a rate limit, a CSRF rejection, and a blocked
account. The server almost always says which in the response body, so the
message carries it.
"""

from __future__ import annotations

from types import SimpleNamespace

from cyberwave_cli.auth import _ERROR_BODY_MAX_LENGTH, _http_error_message


def _response(status_code: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(status_code=status_code, text=text)


def test_includes_the_server_reason() -> None:
    message = _http_error_message(
        _response(403, '{"detail":"Request was throttled. Retry in 42 seconds."}')
    )
    assert "403" in message
    assert "throttled" in message


def test_preserves_the_prefix_the_retry_classifier_matches_on() -> None:
    """``test_python_cli._is_transient_login_failure`` substring-matches this.

    If the body were interpolated before the status code, rate-limit retries
    would silently stop firing.
    """
    message = _http_error_message(_response(429, "slow down"))
    assert message.startswith("HTTP error: 429")
    assert "HTTP error: 429" in message


def test_truncates_a_long_body() -> None:
    """An HTML error page must not be dumped into the terminal."""
    message = _http_error_message(_response(502, "<html>" + ("x" * 5000) + "</html>"))
    # Prefix plus at most the truncation budget.
    assert len(message) <= len("HTTP error: 502: ") + _ERROR_BODY_MAX_LENGTH


def test_collapses_newlines() -> None:
    message = _http_error_message(_response(500, "line one\nline two"))
    assert "\n" not in message
    assert "line one line two" in message


def test_omits_the_separator_for_an_empty_body() -> None:
    assert _http_error_message(_response(404, "")) == "HTTP error: 404"
    assert _http_error_message(_response(404, "   \n ")) == "HTTP error: 404"
