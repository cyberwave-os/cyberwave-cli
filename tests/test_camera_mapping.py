"""Tests for per-twin camera selection and mapping persistence.

These tests exercise :func:`_list_camera_twins` and :func:`_detect_and_select_cameras`
without touching the filesystem in ``CONFIG_DIR`` — instead they patch the helpers
against a ``tmp_path`` isolated config directory.
"""
from __future__ import annotations

import json
from typing import Any

from tests._core_module_loader import load_core_module


def _write_twin_json(config_dir, twin_uuid: str, name: str, *, camera: bool) -> None:
    payload: dict[str, Any] = {
        "uuid": twin_uuid,
        "name": name,
        "asset": {
            "universal_schema": {
                "sensors": (
                    [{"type": "camera", "id": "cam0"}] if camera else []
                ),
            },
        },
    }
    (config_dir / f"{twin_uuid}.json").write_text(json.dumps(payload))


def test_list_camera_twins_returns_only_camera_assets(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, "twin-a", "Cam A", camera=True)
    _write_twin_json(tmp_path, "twin-b", "Arm", camera=False)
    _write_twin_json(tmp_path, "twin-c", "Cam C", camera=True)

    # Reserved files should never be treated as twin caches even when the
    # asset-schema happens to look camera-like.
    (tmp_path / "cameras.json").write_text(
        json.dumps({"asset": {"universal_schema": {"sensors": [{"type": "camera"}]}}})
    )
    (tmp_path / "environment.json").write_text("{}")
    (tmp_path / "credentials.json").write_text("{}")

    twins = core._list_camera_twins()
    assert [t[0] for t in twins] == ["twin-a", "twin-c"]
    assert [t[1] for t in twins] == ["Cam A", "Cam C"]


def test_list_camera_twins_respects_selected_uuids(monkeypatch, tmp_path):
    """Stale caches from previous installs must not leak into the mapping flow."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, "twin-a", "Cam A", camera=True)
    _write_twin_json(tmp_path, "twin-b", "Cam B", camera=True)
    _write_twin_json(tmp_path, "twin-c", "Cam C", camera=True)

    # User only selected twin-a and twin-c in this install run.
    (tmp_path / "environment.json").write_text(
        json.dumps({"twin_uuids": ["twin-a", "twin-c"]})
    )

    twins = core._list_camera_twins()
    assert [t[0] for t in twins] == ["twin-a", "twin-c"]


def test_list_camera_twins_falls_back_when_environment_missing(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, "twin-a", "Cam A", camera=True)
    _write_twin_json(tmp_path, "twin-b", "Cam B", camera=True)

    twins = core._list_camera_twins()
    assert [t[0] for t in twins] == ["twin-a", "twin-b"]


def test_list_camera_twins_falls_back_when_no_twin_uuids_key(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, "twin-a", "Cam A", camera=True)
    (tmp_path / "environment.json").write_text(json.dumps({"name": "env"}))

    twins = core._list_camera_twins()
    assert [t[0] for t in twins] == ["twin-a"]


def test_any_twin_has_camera_sensor_delegates_to_list(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    assert core._any_twin_has_camera_sensor() is False
    _write_twin_json(tmp_path, "twin-a", "Cam A", camera=True)
    assert core._any_twin_has_camera_sensor() is True


class _FakeCam:
    def __init__(self, idx: int, card: str, path: str):
        self.index = idx
        self.card = card
        self.primary_path = path

    def to_dict(self) -> dict:
        return {"index": self.index, "card": self.card, "primary_path": self.primary_path}


def _patch_camera_helpers(
    monkeypatch,
    core,
    cameras: list[_FakeCam],
    prompt_inputs: list[str],
):
    """Patch camera discovery + interactive prompt with deterministic fakes."""
    import cyberwave_cli.device_utils as device_utils

    monkeypatch.setattr(device_utils, "discover_usb_cameras", lambda: cameras)
    monkeypatch.setattr(device_utils, "camera_likelihood_score", lambda _cam: 80)

    iterator = iter(prompt_inputs)
    monkeypatch.setattr(
        core.Prompt,
        "ask",
        lambda *args, **kwargs: next(iterator),
    )
    monkeypatch.setattr(
        core.console,
        "print",
        lambda *args, **kwargs: None,
    )


def test_detect_and_select_cameras_single_physical_auto_assigns_all_twins(
    monkeypatch, tmp_path
):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, "twin-a", "Cam A", camera=True)
    _write_twin_json(tmp_path, "twin-b", "Cam B", camera=True)

    cameras = [_FakeCam(0, "USB Cam", "/dev/video0")]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["selected_device"] == 0
    assert data["twin_to_device"] == {"twin-a": 0, "twin-b": 0}


def test_detect_and_select_cameras_single_twin_keeps_legacy_prompt(
    monkeypatch, tmp_path
):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, "twin-a", "Cam A", camera=True)

    cameras = [
        _FakeCam(0, "Cam A", "/dev/video0"),
        _FakeCam(2, "Cam B", "/dev/video2"),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["2"])

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["selected_device"] == 2
    assert data["twin_to_device"] == {"twin-a": 2}


def test_detect_and_select_cameras_multi_twin_builds_mapping(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, "twin-a", "Cam A", camera=True)
    _write_twin_json(tmp_path, "twin-b", "Cam B", camera=True)

    cameras = [
        _FakeCam(0, "Logitech", "/dev/video0"),
        _FakeCam(1, "USB CAM", "/dev/video1"),
    ]
    _patch_camera_helpers(
        monkeypatch,
        core,
        cameras,
        prompt_inputs=["0", "1"],
    )

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {"twin-a": 0, "twin-b": 1}
    # First mapping is mirrored into the legacy single-select field.
    assert data["selected_device"] == 0


def test_detect_and_select_cameras_invalid_selection_aborts_without_file(
    monkeypatch, tmp_path
):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, "twin-a", "Cam A", camera=True)

    cameras = [
        _FakeCam(0, "Logitech", "/dev/video0"),
        _FakeCam(1, "USB CAM", "/dev/video1"),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["abc"])

    core._detect_and_select_cameras()
    assert not (tmp_path / "cameras.json").exists()
