"""Tests for per-twin camera selection and mapping persistence.

These tests exercise :func:`_list_camera_twins` and :func:`_detect_and_select_cameras`
without touching the filesystem in ``CONFIG_DIR`` — instead they patch the helpers
against a ``tmp_path`` isolated config directory.
"""
from __future__ import annotations

import json
from typing import Any

from tests._core_module_loader import load_core_module

# Twin object files are UUID-named by contract, and `_is_twin_object_file`
# enforces that shape, so these fixtures have to be real UUIDs.
TWIN_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TWIN_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
TWIN_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


def _write_twin_json(
    config_dir,
    twin_uuid: str,
    name: str,
    *,
    camera: bool,
    serial: str | None = None,
    video_device: str | None = None,
    depth: bool = False,
) -> None:
    sensors: list[dict[str, Any]] = []
    if camera:
        sensors.append({"type": "camera", "id": "cam0"})
    if depth:
        # ``universal_schema`` types a depth sensor ``depth_camera``; the
        # ``capabilities`` block types it ``depth``. Both shapes occur, so
        # fixtures carry both to match what real twins look like.
        sensors.append({"type": "depth_camera", "id": "depth0"})
    payload: dict[str, Any] = {
        "uuid": twin_uuid,
        "name": name,
        "asset": {"universal_schema": {"sensors": sensors}},
    }
    if depth:
        payload["capabilities"] = {
            "sensors": [
                {"type": "rgb", "id": "color_camera"},
                {"type": "depth", "id": "depth_camera"},
            ]
        }
    metadata: dict[str, Any] = {}
    if serial is not None:
        metadata["serial_number"] = serial
    if video_device is not None:
        metadata["video_device"] = video_device
    if metadata:
        payload["metadata"] = metadata
    (config_dir / f"{twin_uuid}.json").write_text(json.dumps(payload))


def test_list_camera_twins_returns_only_camera_assets(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    _write_twin_json(tmp_path, TWIN_B, "Arm", camera=False)
    _write_twin_json(tmp_path, TWIN_C, "Cam C", camera=True)

    # Reserved files should never be treated as twin caches even when the
    # asset-schema happens to look camera-like.
    (tmp_path / "cameras.json").write_text(
        json.dumps({"asset": {"universal_schema": {"sensors": [{"type": "camera"}]}}})
    )
    (tmp_path / "environment.json").write_text("{}")
    (tmp_path / "credentials.json").write_text("{}")

    twins = core._list_camera_twins()
    assert [t[0] for t in twins] == [TWIN_A, TWIN_C]
    assert [t[1] for t in twins] == ["Cam A", "Cam C"]


def test_list_camera_twins_respects_selected_uuids(monkeypatch, tmp_path):
    """Stale caches from previous installs must not leak into the mapping flow."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)
    _write_twin_json(tmp_path, TWIN_C, "Cam C", camera=True)

    # User only selected TWIN_A and TWIN_C in this install run.
    (tmp_path / "environment.json").write_text(
        json.dumps({"twin_uuids": [TWIN_A, TWIN_C]})
    )

    twins = core._list_camera_twins()
    assert [t[0] for t in twins] == [TWIN_A, TWIN_C]


def test_list_camera_twins_falls_back_when_environment_missing(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)

    twins = core._list_camera_twins()
    assert [t[0] for t in twins] == [TWIN_A, TWIN_B]


def test_list_camera_twins_falls_back_when_no_twin_uuids_key(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    (tmp_path / "environment.json").write_text(json.dumps({"name": "env"}))

    twins = core._list_camera_twins()
    assert [t[0] for t in twins] == [TWIN_A]


def test_any_twin_has_camera_sensor_delegates_to_list(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    assert core._any_twin_has_camera_sensor() is False
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    assert core._any_twin_has_camera_sensor() is True


class _FakeCam:
    def __init__(
        self,
        idx: int,
        card: str,
        path: str,
        serial: str | None = None,
        paths: list[str] | None = None,
    ):
        self.index = idx
        self.card = card
        self.primary_path = path
        self.paths = paths if paths is not None else [path]
        self.serial = serial

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "card": self.card,
            "primary_path": self.primary_path,
            "serial": self.serial,
        }


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
    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: True)
    # Never reach the network from a unit test; individual tests re-patch it.
    monkeypatch.setattr(core, "_persist_twin_pins", lambda _pins: 0)
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
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)

    cameras = [_FakeCam(0, "USB Cam", "/dev/video0")]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["selected_device"] == 0
    assert data["twin_to_device"] == {TWIN_A: 0, TWIN_B: 0}


def test_detect_and_select_cameras_single_twin_keeps_legacy_prompt(
    monkeypatch, tmp_path
):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)

    cameras = [
        _FakeCam(0, "Cam A", "/dev/video0"),
        _FakeCam(2, "Cam B", "/dev/video2"),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["2"])

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["selected_device"] == 2
    assert data["twin_to_device"] == {TWIN_A: 2}


def test_detect_and_select_cameras_multi_twin_builds_mapping(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)

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
    assert data["twin_to_device"] == {TWIN_A: 0, TWIN_B: 1}
    # First mapping is mirrored into the legacy single-select field.
    assert data["selected_device"] == 0


def test_detect_and_select_cameras_invalid_selection_aborts_without_file(
    monkeypatch, tmp_path
):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)

    cameras = [
        _FakeCam(0, "Logitech", "/dev/video0"),
        _FakeCam(1, "USB CAM", "/dev/video1"),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["abc"])

    core._detect_and_select_cameras()
    assert not (tmp_path / "cameras.json").exists()


# ---- serial-driven binding ---------------------------------------------------
#
# A twin carrying ``metadata.serial_number`` already names its physical camera,
# and discovery reports the same serial per device, so the installer must
# compute the binding instead of asking.  `_prompt_camera_index` is patched to
# raise in these tests: a prompt reaching the operator is the failure.

SERIAL_A = "250223122534"
SERIAL_B = "926223022293"


def _forbid_prompt(monkeypatch, core):
    def _boom(*_args, **_kwargs):
        raise AssertionError("operator was prompted for an already-pinned twin")

    monkeypatch.setattr(core, "_prompt_camera_index", _boom)


def test_load_twin_camera_serials_reads_pinned_twins(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=f"  {SERIAL_A} ")
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)
    _write_twin_json(tmp_path, TWIN_C, "Cam C", camera=True, serial="   ")

    # Whitespace is stripped; blank and absent values are not pins.
    assert core._load_twin_camera_serials() == {TWIN_A: SERIAL_A}


def test_camera_index_for_serial_matches_case_insensitively(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    cameras = [
        _FakeCam(0, "Cam A", "/dev/video0", serial="ab12CD"),
        _FakeCam(6, "Cam B", "/dev/video6", serial=None),
    ]
    assert core._camera_index_for_serial(cameras, " Ab12cd ") == 0
    assert core._camera_index_for_serial(cameras, "nope") is None
    # A camera reporting no serial must never be matched by an empty pin.
    assert core._camera_index_for_serial(cameras, "  ") is None


def test_multi_twin_pinned_serials_bind_without_prompting(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=SERIAL_B)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True, serial=SERIAL_A)

    cameras = [
        _FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    # Each twin follows its own serial, not menu order.
    assert data["twin_to_device"] == {TWIN_A: 6, TWIN_B: 0}


def test_single_twin_pinned_serial_binds_without_prompting(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=SERIAL_B)

    cameras = [
        _FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 6}
    assert data["selected_device"] == 6


def test_pinned_serial_absent_from_host_falls_back_to_prompt(monkeypatch, tmp_path):
    """A serial no attached camera reports must not silently pick a substitute."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial="not-attached")
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True, serial=SERIAL_B)

    cameras = [
        _FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    # Only the unmatched twin is asked; the pinned one resolves on its own.
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["0"])
    monkeypatch.setattr(core, "_persist_twin_pins", lambda _m: 0)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 0, TWIN_B: 6}


def test_manual_mapping_pins_serial_back_onto_twin(monkeypatch, tmp_path):
    """One positional answer becomes stable identity, so it is asked only once."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)

    cameras = [
        _FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["6", "0"])

    recorded: dict[str, str] = {}
    monkeypatch.setattr(
        core,
        "_persist_twin_pins",
        lambda mapping: recorded.update(mapping) or len(mapping),
    )

    core._detect_and_select_cameras()

    assert recorded == {
        TWIN_A: core._camera_pin_metadata(SERIAL_B),
        TWIN_B: core._camera_pin_metadata(SERIAL_A),
    }


def test_manual_mapping_without_discoverable_serial_pins_nothing(monkeypatch, tmp_path):
    """Cameras that report no serial leave the twin unpinned rather than guessing."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)

    cameras = [
        _FakeCam(0, "Webcam", "/dev/video0"),
        _FakeCam(1, "Webcam", "/dev/video1"),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["0", "1"])

    recorded: dict[str, str] = {}
    monkeypatch.setattr(
        core,
        "_persist_twin_pins",
        lambda mapping: recorded.update(mapping) or len(mapping),
    )

    core._detect_and_select_cameras()

    assert recorded == {}
    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 0, TWIN_B: 1}


# ---- device-path binding -----------------------------------------------------
#
# Cameras whose USB descriptor carries no serial (the Logitech C920 reports
# ``iSerial 0``) can never be pinned by ``metadata.serial_number``. udev still
# names them stably under ``/dev/v4l/by-id``, so ``metadata.video_device``
# carrying such a path is identity too and must bind without prompting.

BY_ID_C920 = "/dev/v4l/by-id/usb-046d_HD_Pro_Webcam_C920-video-index0"
BY_PATH_C920 = (
    "/dev/v4l/by-path/platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.3:1.0-video-index0"
)


def _by_id(monkeypatch, core, tmp_path, name: str, target: str | None) -> str:
    """Point the by-id tree at *tmp_path* and return a path inside it.

    A ``target`` of ``None`` leaves the entry absent, which is how an
    unattached camera looks: the pin is well-formed but resolves to nothing.
    """
    by_id = tmp_path / "by-id"
    by_id.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core, "_V4L_BY_ID_DIR", str(by_id))
    link = by_id / name
    if target is not None:
        link.symlink_to(target)
    return str(link)


def test_is_stable_camera_device_rejects_positional_values(monkeypatch):
    core = load_core_module(monkeypatch)
    assert core._is_stable_camera_device(BY_ID_C920) is True
    assert core._is_stable_camera_device(BY_PATH_C920) is True
    assert core._is_stable_camera_device("rtsp://10.0.0.2/avc") is True
    # Positional: "whatever enumerated there this boot" is not identity.
    assert core._is_stable_camera_device("/dev/video6") is False
    assert core._is_stable_camera_device("0") is False
    assert core._is_stable_camera_device("   ") is False


def test_load_twin_camera_devices_reads_only_stable_pins(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    _write_twin_json(
        tmp_path, TWIN_A, "Cam A", camera=True, video_device=f" {BY_ID_C920} "
    )
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True, video_device="/dev/video6")
    _write_twin_json(tmp_path, TWIN_C, "Cam C", camera=True)

    assert core._load_twin_camera_devices() == {TWIN_A: BY_ID_C920}


def test_camera_index_for_device_resolves_symlink_to_any_node(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    # by-id ``-video-index0`` points at the capture node, which is not
    # necessarily the camera's primary_path on multi-node devices.
    link = _by_id(monkeypatch, core, tmp_path, "cam", "/dev/video27")
    cameras = [
        _FakeCam(6, "C920", "/dev/video6"),
        _FakeCam(17, "RealSense", "/dev/video17", paths=["/dev/video17", "/dev/video27"]),
    ]
    assert core._camera_index_for_device(cameras, link) == 17
    missing = str(tmp_path / "by-id" / "missing")
    assert core._camera_index_for_device(cameras, missing) is None
    # A stream is not a local device and never matches one.
    assert core._camera_index_for_device(cameras, "rtsp://10.0.0.2/avc") is None


def test_multi_twin_pinned_device_binds_without_prompting(monkeypatch, tmp_path):
    """The C920 case: no serial in hardware, pinned by udev name instead."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    link = _by_id(monkeypatch, core, tmp_path, "c920", "/dev/video6")
    _write_twin_json(tmp_path, TWIN_A, "C920", camera=True, video_device=link)
    _write_twin_json(tmp_path, TWIN_B, "RealSense", camera=True, serial=SERIAL_A)

    cameras = [
        _FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "C920", "/dev/video6"),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    # The by-id link, not index 6: edge-core renders a stored index back as
    # /dev/videoN, and the driver may substitute away from a positional path.
    assert data["twin_to_device"] == {TWIN_A: link, TWIN_B: 0}


def test_single_twin_pinned_device_binds_without_prompting(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    link = _by_id(monkeypatch, core, tmp_path, "c920", "/dev/video6")
    _write_twin_json(tmp_path, TWIN_A, "C920", camera=True, video_device=link)

    cameras = [
        _FakeCam(0, "Other", "/dev/video0"),
        _FakeCam(6, "C920", "/dev/video6"),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: link}
    # selected_device stays an index: it is the legacy global fallback for
    # twins with no mapping of their own, and older edge-core builds read it.
    assert data["selected_device"] == 6


def test_serial_wins_over_device_when_twin_pins_both(monkeypatch, tmp_path):
    """``serial_number`` is the stronger identity and must not be overridden."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    link = _by_id(monkeypatch, core, tmp_path, "other", "/dev/video0")
    _write_twin_json(
        tmp_path, TWIN_A, "Cam A", camera=True, serial=SERIAL_B, video_device=link
    )

    cameras = [
        _FakeCam(0, "Other", "/dev/video0"),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 6}


def test_pinned_device_absent_from_host_falls_back_to_prompt(monkeypatch, tmp_path):
    """A dangling pin must not silently bind whatever else is attached."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    missing = _by_id(monkeypatch, core, tmp_path, "not-attached", None)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, video_device=missing)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True, serial=SERIAL_B)

    cameras = [
        _FakeCam(0, "Webcam", "/dev/video0"),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    # Only the unresolvable twin is asked.
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["0"])
    monkeypatch.setattr(core, "_persist_twin_pins", lambda _m: 0)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 0, TWIN_B: 6}


def test_device_pinned_twin_never_learns_a_serial(monkeypatch, tmp_path):
    """An already-pinned twin must not have a second identity written onto it."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    link = _by_id(monkeypatch, core, tmp_path, "c920", "/dev/video6")
    _write_twin_json(tmp_path, TWIN_A, "C920", camera=True, video_device=link)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)

    cameras = [
        _FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "C920", "/dev/video6"),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["0"])

    recorded: dict[str, str] = {}
    monkeypatch.setattr(
        core,
        "_persist_twin_pins",
        lambda mapping: recorded.update(mapping) or len(mapping),
    )

    core._detect_and_select_cameras()

    # Only the twin that was actually asked learns a serial.
    assert recorded == {TWIN_B: core._camera_pin_metadata(SERIAL_A)}


def test_stream_pinned_twin_is_not_given_a_local_camera(monkeypatch, tmp_path):
    """An IP camera twin needs no /dev/video* mapping and must not be asked."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    _write_twin_json(
        tmp_path, TWIN_A, "IP Cam", camera=True, video_device="rtsp://10.0.0.2/avc"
    )
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True, serial=SERIAL_B)

    cameras = [
        _FakeCam(0, "Webcam", "/dev/video0"),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    # Recorded explicitly rather than omitted: edge-core falls back to the
    # global selected_device for any twin missing from the mapping, so leaving
    # the stream twin out would inject a local /dev/video* ahead of its URL.
    assert data["twin_to_device"] == {TWIN_A: "rtsp://10.0.0.2/avc", TWIN_B: 6}


def test_single_camera_does_not_absorb_stream_pinned_twin(monkeypatch, tmp_path):
    """The auto-assign-everything path must still respect a stream pin."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    _write_twin_json(
        tmp_path, TWIN_A, "IP Cam", camera=True, video_device="rtsp://10.0.0.2/avc"
    )
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)

    cameras = [_FakeCam(0, "USB Cam", "/dev/video0")]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: "rtsp://10.0.0.2/avc", TWIN_B: 0}


# ---------------------------------------------------------------------------
# Regressions for the pin -> cameras.json -> edge-core -> driver round trip.
#
# Asserting on cameras.json alone is not enough: every one of these bugs kept
# a correct-looking mapping while the value in it was the wrong *kind*, and
# the damage only appeared once edge-core rendered it back into an env var.
# ---------------------------------------------------------------------------


def test_device_pin_survives_as_a_stable_identifier(monkeypatch, tmp_path):
    """A by-id pin must not be persisted as an index.

    edge-core turns a stored index into ``/dev/video{N}`` and injects it as
    ``CYBERWAVE_METADATA_VIDEO_DEVICE``; the driver's entrypoint then skips
    exporting the twin's own ``metadata.video_device`` because the variable is
    already set. The driver classes ``/dev/videoN`` as positional and is free
    to substitute another camera -- so persisting the index silently undoes
    the pin.
    """
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    link = _by_id(monkeypatch, core, tmp_path, "c920", "/dev/video6")
    _write_twin_json(tmp_path, TWIN_A, "C920", camera=True, video_device=link)

    cameras = [_FakeCam(0, "Other", "/dev/video0"), _FakeCam(6, "C920", "/dev/video6")]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    stored = json.loads((tmp_path / "cameras.json").read_text())["twin_to_device"][TWIN_A]
    assert isinstance(stored, str)
    assert core._is_stable_camera_device(stored)


def test_stream_pin_is_recorded_so_it_cannot_inherit_the_global_device(
    monkeypatch, tmp_path
):
    """Omitting a twin from the mapping is not the same as excluding it.

    ``_load_selected_camera_device`` falls back to the global
    ``selected_device`` for any twin it cannot find in ``twin_to_device``, so
    a stream twin left out of the mapping still gets ``/dev/video0`` injected
    ahead of its own metadata.
    """
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    _write_twin_json(
        tmp_path, TWIN_A, "IP Cam", camera=True, video_device="rtsp://10.0.0.2/avc"
    )
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True, serial=SERIAL_B)

    cameras = [
        _FakeCam(0, "Webcam", "/dev/video0"),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    mapping = json.loads((tmp_path / "cameras.json").read_text())["twin_to_device"]
    assert TWIN_A in mapping, "stream twin must be pinned, not left to the fallback"
    assert mapping[TWIN_A] == "rtsp://10.0.0.2/avc"


def test_single_twin_stale_device_pin_is_replaced_by_the_operators_choice(
    monkeypatch, tmp_path
):
    """A pin whose camera is not attached forces a fresh choice, which is saved.

    The operator is shown the attached cameras and picks one explicitly, so
    persisting that answer is the point: leaving the stale pin in place would
    ask the same question on every install.
    """
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    # Pinned to a camera that is not attached during this run.
    link = _by_id(monkeypatch, core, tmp_path, "c920", "/dev/video6")
    _write_twin_json(tmp_path, TWIN_A, "C920", camera=True, video_device=link)

    recorded: dict[str, str] = {}

    cameras = [
        _FakeCam(0, "Some Other Cam", "/dev/video0", serial=SERIAL_A),
        _FakeCam(1, "Spare", "/dev/video1"),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["0"])
    monkeypatch.setattr(core, "_persist_twin_pins", recorded.update)

    core._detect_and_select_cameras()

    assert recorded == {TWIN_A: core._camera_pin_metadata(SERIAL_A)}


def test_stable_camera_device_mirrors_the_driver_classification(monkeypatch):
    """Anything the driver refuses to substitute must read as pinned here.

    The driver's rule is positional-or-not, not an allow-list of schemes: a
    bare index and a ``/dev/...`` node are positional, everything else names a
    device. A narrower rule here lets a learned serial overwrite a pin the
    driver would have honoured.
    """
    core = load_core_module(monkeypatch)

    # Non-RTSP transports are just as much a pinned source.
    assert core._is_stable_camera_device("udp://239.0.0.1:5000") is True
    assert core._is_stable_camera_device("tcp://10.0.0.9:8554/live") is True

    # ...but they are not local devices, so they never claim a /dev node.
    assert core._is_local_stable_camera_device("udp://239.0.0.1:5000") is False
    assert core._is_local_stable_camera_device(BY_ID_C920) is True

    # Positional values stay positional.
    assert core._is_stable_camera_device("/dev/video6") is False
    assert core._is_stable_camera_device("12") is False


# ---------------------------------------------------------------------------
# Depth twins: an unmatched serial is not proof of a wrong pin.
#
# A RealSense reports two unrelated serials -- the USB descriptor one that
# v4l2-ctl surfaces, and librealsense's own, which no host tool can read. A pin
# carrying the latter cannot be matched here, but is perfectly valid.
# ---------------------------------------------------------------------------


def test_depth_twin_with_unverifiable_serial_is_not_prompted(monkeypatch, tmp_path):
    """The regression: a valid librealsense pin must not trigger a prompt.

    Prompting would also be useless -- ``_resolve_camera_source`` ignores the
    resulting ``/dev/videoN`` on the depth path.
    """
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    # 213722070420 is a librealsense serial; the cameras report USB serials.
    _write_twin_json(
        tmp_path, TWIN_A, "RealSense", camera=True, depth=True, serial="213722070420"
    )
    _write_twin_json(tmp_path, TWIN_B, "Webcam", camera=True, serial=SERIAL_B)

    cameras = [
        _FakeCam(0, "Intel RealSense", "/dev/video0", serial="214523026669"),
        _FakeCam(6, "Webcam", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    mapping = json.loads((tmp_path / "cameras.json").read_text())["twin_to_device"]
    # Persisted with the ``serial:`` tag, not as a bare value: edge-core coerces
    # an all-digit string to an index, so an untagged serial rendered as
    # /dev/video213722070420. An absent entry would inherit selected_device.
    assert mapping[TWIN_A] == "serial:213722070420"
    assert mapping[TWIN_B] == 6


def test_non_depth_twin_with_unmatched_serial_still_prompts(monkeypatch, tmp_path):
    """The leniency is scoped to depth twins.

    A UVC camera has exactly one serial, so an unmatched value really is wrong
    and the operator must still be asked.
    """
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    _write_twin_json(tmp_path, TWIN_A, "Webcam", camera=True, serial="NOPE-NOT-HERE")
    _write_twin_json(tmp_path, TWIN_B, "Other", camera=True, serial=SERIAL_B)

    cameras = [
        _FakeCam(0, "Webcam", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "Other", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["0"])

    core._detect_and_select_cameras()

    mapping = json.loads((tmp_path / "cameras.json").read_text())["twin_to_device"]
    assert mapping[TWIN_A] == 0  # answered by the prompt, not silently pinned


def test_depth_twin_with_matching_serial_binds_to_the_index(monkeypatch, tmp_path):
    """Leniency must not shadow the ordinary path.

    When the pin *is* a USB serial the host can see, it resolves to a real
    camera and binds normally.
    """
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    _write_twin_json(
        tmp_path, TWIN_A, "RealSense", camera=True, depth=True, serial="214523026669"
    )
    _write_twin_json(tmp_path, TWIN_B, "Webcam", camera=True, serial=SERIAL_B)

    cameras = [
        _FakeCam(0, "Intel RealSense", "/dev/video0", serial="214523026669"),
        _FakeCam(6, "Webcam", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    mapping = json.loads((tmp_path / "cameras.json").read_text())["twin_to_device"]
    assert mapping[TWIN_A] == 0


def test_depth_twin_never_learns_a_serial_over_its_pin(monkeypatch, tmp_path):
    """An unverifiable pin must not be overwritten by a discovered serial.

    Writing the USB serial of whatever the operator picked would re-pin the
    twin to a different camera, permanently.
    """
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    _write_twin_json(
        tmp_path, TWIN_A, "RealSense", camera=True, depth=True, serial="213722070420"
    )

    recorded: dict[str, str] = {}

    cameras = [
        _FakeCam(0, "Intel RealSense", "/dev/video0", serial="214523026669"),
        _FakeCam(6, "Spare", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    monkeypatch.setattr(core, "_persist_twin_pins", recorded.update)
    _forbid_prompt(monkeypatch, core)

    core._detect_and_select_cameras()

    assert recorded == {}


def test_depth_detection_reads_both_sensor_vocabularies(monkeypatch, tmp_path):
    """``universal_schema`` says ``depth_camera``; ``capabilities`` says ``depth``."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    (tmp_path / f"{TWIN_A}.json").write_text(
        json.dumps(
            {
                "uuid": TWIN_A,
                "name": "schema-only",
                "asset": {"universal_schema": {"sensors": [{"type": "depth_camera"}]}},
            }
        )
    )
    (tmp_path / f"{TWIN_B}.json").write_text(
        json.dumps(
            {
                "uuid": TWIN_B,
                "name": "capabilities-only",
                "capabilities": {"sensors": [{"type": "depth", "id": "depth_camera"}]},
            }
        )
    )
    (tmp_path / f"{TWIN_C}.json").write_text(
        json.dumps(
            {
                "uuid": TWIN_C,
                "name": "rgb-only",
                "asset": {"universal_schema": {"sensors": [{"type": "camera"}]}},
                "capabilities": {"sensors": [{"type": "rgb", "id": "color_camera"}]},
            }
        )
    )

    assert core._twin_has_depth_sensor(TWIN_A) is True
    assert core._twin_has_depth_sensor(TWIN_B) is True
    assert core._twin_has_depth_sensor(TWIN_C) is False
    assert core._twin_has_depth_sensor("no-such-twin") is False


def test_declining_existing_pins_reprompts_and_repins(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=SERIAL_B)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True, serial=SERIAL_A)
    cameras = [
        _FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["0", "6"])
    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: False)
    persisted: dict = {}
    monkeypatch.setattr(
        core, "_persist_twin_pins", lambda m: persisted.update(m) or len(m)
    )

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 0, TWIN_B: 6}
    assert persisted == {
        TWIN_A: core._camera_pin_metadata(SERIAL_A),
        TWIN_B: core._camera_pin_metadata(SERIAL_B),
    }


def test_declining_existing_pin_single_twin_reprompts(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=SERIAL_B)
    cameras = [
        _FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["0"])
    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: False)
    persisted: dict = {}
    monkeypatch.setattr(
        core, "_persist_twin_pins", lambda m: persisted.update(m) or len(m)
    )

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 0}
    assert persisted == {TWIN_A: core._camera_pin_metadata(SERIAL_A)}


def test_no_confirmation_asked_without_existing_pins(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)
    cameras = [_FakeCam(0, "A", "/dev/video0"), _FakeCam(1, "B", "/dev/video1")]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["0", "1"])

    def _boom(*a, **k):
        raise AssertionError("Confirm must not be asked without pins")

    monkeypatch.setattr(core.Confirm, "ask", _boom)
    core._detect_and_select_cameras()


def test_keeping_existing_pins_uses_them_without_a_menu(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=SERIAL_B)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True, serial=SERIAL_A)
    cameras = [
        _FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)
    asked: list[str] = []
    monkeypatch.setattr(core.Confirm, "ask", lambda p, **k: asked.append(p) or True)

    core._detect_and_select_cameras()

    assert asked == ["Keep these existing camera bindings?"]
    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 6, TWIN_B: 0}


def test_single_physical_camera_never_asks_to_keep(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=SERIAL_A)
    cameras = [_FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A)]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])

    def _boom(*_args, **_kwargs):
        raise AssertionError("a single camera has nothing to keep or redo")

    monkeypatch.setattr(core.Confirm, "ask", _boom)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 0}


def test_closed_stdin_keeps_existing_pins(monkeypatch, tmp_path):
    """Scripted installs (no TTY) must behave as before the prompt existed."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=SERIAL_B)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True, serial=SERIAL_A)
    cameras = [
        _FakeCam(0, "RealSense", "/dev/video0", serial=SERIAL_A),
        _FakeCam(6, "RealSense", "/dev/video6", serial=SERIAL_B),
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])
    _forbid_prompt(monkeypatch, core)

    def _eof(*_args, **_kwargs):
        raise EOFError

    monkeypatch.setattr(core.Confirm, "ask", _eof)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 6, TWIN_B: 0}
