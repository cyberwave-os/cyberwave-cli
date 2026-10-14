"""Tests for :func:`_download_twin_json_files`, the twin cache writer.

``_load_twin_camera_serials`` and every other cache reader resolve
``metadata`` out of the ``{twin_uuid}.json`` files this function writes, so a
field dropped here is a feature that silently never fires.  The camera-mapping
tests hand-build those files, which means they cannot catch a writer that
truncates — these tests drive the writer itself.
"""
from __future__ import annotations

import json
from typing import Any

from tests._core_module_loader import load_core_module

TWIN_UUID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
ASSET_UUID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


class _FakeTwinSchema:
    """Stands in for the generated ``TwinSchema`` returned by ``get_raw``.

    Only the surface ``_download_twin_json_files`` touches: ``to_dict()`` plus
    the ``asset_uuid`` / ``name`` attributes it reads directly.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.name = payload.get("name")
        self.asset_uuid = payload.get("asset_uuid")

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _FakeRichTwin:
    """Stands in for the ``Twin`` handle returned by ``twins.get()``.

    Deliberately defines no ``to_dict`` — that absence is the bug this module
    guards against.  A writer that calls ``get()`` gets this object and can
    only produce a stub.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self.name = payload.get("name")
        self.asset_uuid = payload.get("asset_uuid")


class _FakeAsset:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


def _twin_payload() -> dict[str, Any]:
    return {
        "uuid": TWIN_UUID,
        "name": "Standard Camera",
        "asset_uuid": ASSET_UUID,
        "metadata": {
            "serial_number": "A260203000509667",
            "edge_fingerprint": "fingerprint-test",
        },
        "capabilities": {"sensors": [{"type": "rgb", "id": "color_camera"}]},
    }


class _FakeClient:
    """Records which accessor the writer reached for."""

    def __init__(self) -> None:
        payload = _twin_payload()
        self.calls: list[str] = []
        outer = self

        class _Twins:
            def get(self, twin_uuid: str) -> _FakeRichTwin:
                outer.calls.append("get")
                return _FakeRichTwin(payload)

            def get_raw(self, twin_uuid: str) -> _FakeTwinSchema:
                outer.calls.append("get_raw")
                return _FakeTwinSchema(payload)

        class _Assets:
            def get(self, asset_uuid: str) -> _FakeAsset:
                return _FakeAsset({"uuid": asset_uuid, "name": "Standard Camera"})

        self.twins = _Twins()
        self.assets = _Assets()


def _run_writer(monkeypatch, tmp_path) -> tuple[Any, _FakeClient, dict[str, Any]]:
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "chown_to_sudo_user", lambda *a, **k: None)

    client = _FakeClient()
    written = core._download_twin_json_files(client, [TWIN_UUID])
    assert written == 1

    cached = json.loads((tmp_path / f"{TWIN_UUID}.json").read_text())
    return core, client, cached


def test_cached_twin_preserves_metadata(monkeypatch, tmp_path):
    """The writer must persist ``metadata`` — the serial auto-bind reads it.

    Regression: the writer called ``twins.get()``, whose ``Twin`` handle has no
    ``to_dict``, so a ``hasattr`` ternary fell through to a ``{uuid, name}``
    stub.  ``metadata.serial_number`` never reached disk and every camera twin
    was prompted for despite being pinned.
    """
    _, _, cached = _run_writer(monkeypatch, tmp_path)

    assert cached["metadata"]["serial_number"] == "A260203000509667"


def test_cached_twin_is_not_a_stub(monkeypatch, tmp_path):
    """Guard the shape, not just the one field that regressed.

    The stub was hard to spot because ``twin_data["asset"]`` refilled the file
    to a plausible size.  Asserting a twin-level field beyond ``uuid``/``name``
    catches a truncating writer even when the asset blob looks healthy.
    """
    _, _, cached = _run_writer(monkeypatch, tmp_path)

    assert cached["capabilities"]["sensors"][0]["type"] == "rgb"
    assert cached["asset"]["uuid"] == ASSET_UUID


def test_writer_uses_get_raw(monkeypatch, tmp_path):
    """Pin the accessor: ``get()`` cannot serialize a full twin."""
    _, client, _ = _run_writer(monkeypatch, tmp_path)

    assert client.calls == ["get_raw"]


def test_cached_serial_is_visible_to_the_mapping_reader(monkeypatch, tmp_path):
    """Close the loop the existing tests leave open.

    ``test_camera_mapping`` writes cache files by hand, so it proves the reader
    works against a shape the writer never produced.  Round-tripping the real
    writer into the real reader is what actually guarantees the auto-bind path
    is reachable.
    """
    core, _, _ = _run_writer(monkeypatch, tmp_path)

    assert core._load_twin_camera_serials() == {TWIN_UUID: "A260203000509667"}


def test_cached_twin_is_written_atomically_and_world_readable(monkeypatch, tmp_path):
    """A watcher reading the file must never see a half-written twin JSON."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "chown_to_sudo_user", lambda *a, **k: None)
    seen: list[tuple[Any, int]] = []
    real = core.atomic_write_json

    def spy(path, data, *, mode=0o600):
        seen.append((path, mode))
        real(path, data, mode=mode)

    monkeypatch.setattr(core, "atomic_write_json", spy)

    assert core._download_twin_json_files(_FakeClient(), [TWIN_UUID]) == 1

    assert seen == [(tmp_path / f"{TWIN_UUID}.json", 0o644)]
    assert list(tmp_path.glob(".*.tmp")) == []
