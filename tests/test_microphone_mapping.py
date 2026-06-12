"""Tests for per-twin microphone selection helpers (mirrors camera mapping)."""

from __future__ import annotations

import json
from typing import Any

from tests._core_module_loader import load_core_module


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

    _write_twin_json(tmp_path, "twin-a", "Mic A", sensor_type="audio")
    _write_twin_json(tmp_path, "twin-b", "Arm", sensor_type="joint")
    (tmp_path / "twin-b.json").write_text(
        json.dumps(
            {
                "uuid": "twin-b",
                "name": "Arm",
                "asset": {"universal_schema": {"sensors": [{"type": "joint"}]}},
            }
        )
    )
    _write_twin_json(tmp_path, "twin-c", "Mic C", sensor_type="audio_mono")

    (tmp_path / "audio_streams.json").write_text("{}")
    (tmp_path / "environment.json").write_text("{}")

    twins = core._list_microphone_twins()
    assert [t[0] for t in twins] == ["twin-a", "twin-c"]


def test_list_microphone_twins_respects_selected_uuids(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, "twin-a", "Mic A")
    _write_twin_json(tmp_path, "twin-b", "Mic B")
    (tmp_path / "environment.json").write_text(
        json.dumps({"twin_uuids": ["twin-a"]})
    )

    twins = core._list_microphone_twins()
    assert [t[0] for t in twins] == ["twin-a"]


def test_any_twin_has_microphone_sensor_delegates_to_list(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    assert core._any_twin_has_microphone_sensor() is False
    _write_twin_json(tmp_path, "twin-a", "Mic A")
    assert core._any_twin_has_microphone_sensor() is True


def test_list_microphone_twins_reads_twin_metadata_sensors(monkeypatch, tmp_path):
    """Catalog twins often declare sensors under metadata, not universal_schema."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")
    (tmp_path / "environment.json").write_text("{}")

    (tmp_path / "twin-meta.json").write_text(
        json.dumps(
            {
                "uuid": "twin-meta",
                "name": "Mic Meta",
                "asset": {"universal_schema": {"sensors": []}},
                "metadata": {
                    "sensors": [{"type": "audio", "id": "audio", "name": "audio"}],
                },
            }
        )
    )

    twins = core._list_microphone_twins()
    assert twins == [("twin-meta", "Mic Meta")]
