from __future__ import annotations

import errno
import re
import socket
from urllib.parse import urlsplit

import click

_USERINFO = re.compile(r"(^|//)[^/?#]*@")

_ERRNO_REASONS = {
    errno.EHOSTUNREACH: "no route to host",
    errno.ENETUNREACH: "network unreachable",
    errno.ECONNREFUSED: "connection refused",
    errno.ETIMEDOUT: "timed out",
}
_TEXT_REASONS = (
    ("no route to host", "no route to host"),
    ("network is unreachable", "network unreachable"),
    ("connection refused", "connection refused"),
    ("name or service not known", "host name not found"),
    ("nodename nor servname", "host name not found"),
    ("temporary failure in name resolution", "host name not found"),
    ("getaddrinfo failed", "host name not found"),
    ("timed out", "timed out"),
    ("failed to establish a new connection", "connection failed"),
    ("max retries exceeded", "connection failed"),
    ("connection error", "connection failed"),
)
_ERRNO_IN_TEXT = re.compile(r"\[Errno (-?\d+)\]")

# Web app hosts, mapped to the API that serves the same environment.
_WEB_APP_API_URLS = {
    "cyberwave.com": "https://api.cyberwave.com",
    "www.cyberwave.com": "https://api.cyberwave.com",
    "app-dev.cyberwave.com": "https://api-dev.cyberwave.com",
    "app-staging.cyberwave.com": "https://api-staging.cyberwave.com",
}


def redact_userinfo(text: str) -> str:
    return _USERINFO.sub(r"\1***@", text)


def require_valid_url(url: str, source: str) -> str:
    text = (url or "").strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        parts, port = None, None
        valid = False
    else:
        valid = parts.scheme in ("http", "https") and bool(parts.hostname)
        valid = valid and (port is None or port > 0)
    shown = redact_userinfo(url or "")
    if not valid:
        raise click.ClickException(f"{source} is not a valid URL: {shown!r}")
    api_url = _WEB_APP_API_URLS.get(parts.hostname)
    if api_url:
        raise click.ClickException(
            f"{source} is the Cyberwave web app, not its API: {shown!r}. Use {api_url}"
        )
    return text


def unreachable_reason(exc: BaseException) -> str | None:
    """Short reason when *exc* means the backend could not be reached at all, else None."""
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    for item in chain:
        if isinstance(item, socket.gaierror):
            return "host name not found"
        if isinstance(item, OSError) and item.errno in _ERRNO_REASONS:
            return _ERRNO_REASONS[item.errno]
    text = " ".join(str(item) for item in chain)
    for match in _ERRNO_IN_TEXT.finditer(text):
        reason = _ERRNO_REASONS.get(int(match.group(1)))
        if reason:
            return reason
    lowered = text.lower()
    return next((reason for needle, reason in _TEXT_REASONS if needle in lowered), None)


def backend_line(base_url: str | None) -> str | None:
    """A dim "Backend: <url>" line for any backend but production, which needs no mention."""
    from cyberwave.config import DEFAULT_BASE_URL
    from rich.markup import escape

    if not base_url or base_url.rstrip("/") == DEFAULT_BASE_URL.rstrip("/"):
        return None
    return f"[dim]Backend: {escape(redact_userinfo(base_url))}[/dim]"


def backend_unreachable_message(base_url: str, reason: str) -> str:
    from rich.markup import escape

    shown = redact_userinfo(base_url)
    return (
        f"[red]✗[/red] Can't reach the Cyberwave backend at [bold]{escape(shown)}[/bold] "
        f"[dim]({reason})[/dim]\n"
        "  [dim]→ Is it running at that address? Point CYBERWAVE_BASE_URL at the right one "
        "and retry.[/dim]"
    )
