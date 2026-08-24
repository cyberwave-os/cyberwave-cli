"""Tests for per-twin microphone selection helpers (mirrors camera mapping)."""

from __future__ import annotations

import json
from typing import Any

from tests._core_module_loader import load_core_module

# Twin object files are UUID-named by contract, and `_is_twin_object_file`
# enforces that shape, so these fixtures have to be real UUIDs.
TWIN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TWIN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TWIN_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
TWIN_META = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def _write_twin_json(
    config_dir,
    twin_uuid: str,
    name: str,
    *,
    sensor_type: str = "audio",
) -> None:
    payload: dict[str, Any] = {
        "uuid": twin_uuid,
        "name": name,
        "asset": {
            "universal_schema": {
                "sensors": [{"type": sensor_type, "id": "mic0"}],
            },
        },
    }
    (config_dir / f"{twin_uuid}.json").write_text(json.dumps(payload))


def test_list_microphone_twins_returns_only_audio_assets(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, TWIN_A, "Mic A", sensor_type="audio")
    _write_twin_json(tmp_path, TWIN_B, "Arm", sensor_type="joint")
    (tmp_path / f"{TWIN_B}.json").write_text(
        json.dumps(
            {
                "uuid": TWIN_B,
                "name": "Arm",
                "asset": {"universal_schema": {"sensors": [{"type": "joint"}]}},
            }
        )
    )
    _write_twin_json(tmp_path, TWIN_C, "Mic C", sensor_type="audio_mono")

    (tmp_path / "audio_streams.json").write_text("{}")
    (tmp_path / "environment.json").write_text("{}")

    twins = core._list_microphone_twins()
    assert [t[0] for t in twins] == [TWIN_A, TWIN_C]


def test_list_microphone_twins_respects_selected_uuids(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, TWIN_A, "Mic A")
    _write_twin_json(tmp_path, TWIN_B, "Mic B")
    (tmp_path / "environment.json").write_text(
        json.dumps({"twin_uuids": [TWIN_A]})
    )

    twins = core._list_microphone_twins()
    assert [t[0] for t in twins] == [TWIN_A]


def test_any_twin_has_microphone_sensor_delegates_to_list(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    assert core._any_twin_has_microphone_sensor() is False
    _write_twin_json(tmp_path, TWIN_A, "Mic A")
    assert core._any_twin_has_microphone_sensor() is True


def test_list_microphone_twins_reads_twin_metadata_sensors(monkeypatch, tmp_path):
    """Catalog twins often declare sensors under metadata, not universal_schema."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")
    (tmp_path / "environment.json").write_text("{}")

    (tmp_path / f"{TWIN_META}.json").write_text(
        json.dumps(
            {
                "uuid": TWIN_META,
                "name": "Mic Meta",
                "asset": {"universal_schema": {"sensors": []}},
                "metadata": {
                    "sensors": [{"type": "audio", "id": "audio", "name": "audio"}],
                },
            }
        )
    )

    twins = core._list_microphone_twins()
    assert twins == [(TWIN_META, "Mic Meta")]
