"""URL validation shared by the commands that talk to the API."""

from __future__ import annotations

from urllib.parse import urlsplit

import click


def require_valid_url(url: str, source: str) -> str:
    """Return *url* stripped, or fail with a readable error.

    Valid means an http(s) URL with a host and a usable port, so a typo in
    CYBERWAVE_BASE_URL never reaches httpx as a traceback.
    """
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
    if not valid:
        raise click.ClickException(f"{source} is not a valid URL: {url!r}")
    return text
