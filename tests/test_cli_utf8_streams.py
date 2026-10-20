"""A redirected stream on Windows defaults to cp1252, which cannot encode the CLI's glyphs."""

from __future__ import annotations

import io
import sys

import pytest
from rich.console import Console

from cyberwave_cli.main import _use_utf8_streams


def _cp1252_stream() -> tuple[io.BytesIO, io.TextIOWrapper]:
    raw = io.BytesIO()
    return raw, io.TextIOWrapper(raw, encoding="cp1252", write_through=True)


def test_check_mark_is_unencodable_before_the_fix() -> None:
    _, stream = _cp1252_stream()
    with pytest.raises(UnicodeEncodeError):
        Console(file=stream, force_terminal=False).print("[green]✓[/green] ok")


def test_use_utf8_streams_lets_rich_print_the_check_mark(monkeypatch: pytest.MonkeyPatch) -> None:
    raw, stream = _cp1252_stream()
    monkeypatch.setattr(sys, "stdout", stream)
    _use_utf8_streams()
    Console(file=sys.stdout, force_terminal=False).print("[green]✓[/green] ok")
    assert "✓".encode() in raw.getvalue()


def test_use_utf8_streams_tolerates_streams_without_reconfigure(monkeypatch: pytest.MonkeyPatch) -> None:
    class Bare:
        encoding = "cp1252"

    monkeypatch.setattr(sys, "stdout", Bare())
    _use_utf8_streams()
