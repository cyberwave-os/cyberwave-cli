"""Tests for device_utils — especially the PyInstaller LD_LIBRARY_PATH fix.

The bundled CLI sets LD_LIBRARY_PATH to the _MEIPASS extraction dir.
System tools like v4l2-ctl must NOT inherit that path, otherwise they
load the wrong libstdc++ and fail with errors like:
    GLIBCXX_3.4.32 not found
"""

import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from cyberwave_cli.device_utils import (
    CameraDevice,
    _get_v4l2_device_info,
    _parse_v4l2_list_devices,
    camera_likelihood_score,
    discover_usb_cameras,
    discover_usb_cameras_v4l2,
)

FAKE_MEIPASS = "/tmp/_MEIfakedir"


# ---------------------------------------------------------------------------
# _parse_v4l2_list_devices (pure parsing, no subprocess)
# ---------------------------------------------------------------------------

def test_parse_v4l2_list_devices_basic():
    output = (
        "HD USB Camera: HD USB Camera (usb-0000:01:00.0-1.2):\n"
        "\t/dev/video0\n"
        "\t/dev/video1\n"
        "\t/dev/media0\n"
        "\n"
        "Logitech C920 (usb-0000:01:00.0-1.4):\n"
        "\t/dev/video2\n"
        "\t/dev/video3\n"
        "\t/dev/media1\n"
    )
    devices = _parse_v4l2_list_devices(output)
    assert len(devices) == 2
    assert devices[0].card == "HD USB Camera: HD USB Camera"
    assert devices[0].paths == ["/dev/video0", "/dev/video1"]
    assert devices[1].card == "Logitech C920"
    assert devices[1].paths == ["/dev/video2", "/dev/video3"]


def test_parse_v4l2_list_devices_empty():
    assert _parse_v4l2_list_devices("") == []


# ---------------------------------------------------------------------------
# Subprocess env isolation — the core of the RPi GLIBCXX fix
# ---------------------------------------------------------------------------

def _assert_env_clean(call_args) -> dict:
    """Extract the env= kwarg from a subprocess.run mock call and verify it."""
    env = call_args.kwargs.get("env") or call_args[1].get("env")
    assert env is not None, "subprocess.run was called without env= (LD_LIBRARY_PATH leak)"
    ld = env.get("LD_LIBRARY_PATH", "")
    assert FAKE_MEIPASS not in ld.split(os.pathsep), (
        f"LD_LIBRARY_PATH still contains _MEIPASS dir: {ld}"
    )
    return env


@patch("cyberwave_cli.device_utils.shutil.which", return_value="/usr/bin/v4l2-ctl")
@patch("cyberwave_cli.device_utils._ensure_video_device_permissions")
@patch("cyberwave_cli.device_utils.subprocess.run")
def test_discover_v4l2_strips_meipass_from_env(mock_run, _mock_perms, _mock_which):
    """Simulate PyInstaller bundle: _MEIPASS set, LD_LIBRARY_PATH polluted."""
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="USB Cam (usb-0):\n\t/dev/video0\n",
        stderr="",
    )

    polluted_ld = f"{FAKE_MEIPASS}:/usr/lib/aarch64-linux-gnu"

    with (
        patch.dict(os.environ, {"LD_LIBRARY_PATH": polluted_ld}, clear=False),
        patch.object(sys, "_MEIPASS", FAKE_MEIPASS, create=True),
    ):
        devices = discover_usb_cameras_v4l2()

    assert mock_run.called
    _assert_env_clean(mock_run.call_args)
    assert len(devices) == 1


@patch("cyberwave_cli.device_utils.shutil.which", return_value="/usr/bin/v4l2-ctl")
@patch("cyberwave_cli.device_utils.subprocess.run")
def test_get_v4l2_device_info_strips_meipass(mock_run, _mock_which):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout="Driver name   : uvcvideo\nCard type     : USB Cam\nBus info      : usb-0\n",
        stderr="",
    )

    polluted_ld = f"{FAKE_MEIPASS}:/usr/lib/aarch64-linux-gnu"

    with (
        patch.dict(os.environ, {"LD_LIBRARY_PATH": polluted_ld}, clear=False),
        patch.object(sys, "_MEIPASS", FAKE_MEIPASS, create=True),
    ):
        info = _get_v4l2_device_info("/dev/video0")

    _assert_env_clean(mock_run.call_args)
    assert info["driver"] == "uvcvideo"


@patch("cyberwave_cli.device_utils.shutil.which", return_value="/usr/bin/v4l2-ctl")
@patch("cyberwave_cli.device_utils._ensure_video_device_permissions")
@patch("cyberwave_cli.device_utils.subprocess.run")
def test_discover_v4l2_restores_orig_ld_library_path(mock_run, _mock_perms, _mock_which):
    """PyInstaller saves the original value in LD_LIBRARY_PATH_ORIG."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout="", stderr="",
    )

    original_ld = "/usr/lib/aarch64-linux-gnu"

    with (
        patch.dict(
            os.environ,
            {
                "LD_LIBRARY_PATH": FAKE_MEIPASS,
                "LD_LIBRARY_PATH_ORIG": original_ld,
            },
            clear=False,
        ),
        patch.object(sys, "_MEIPASS", FAKE_MEIPASS, create=True),
    ):
        discover_usb_cameras_v4l2()

    env = mock_run.call_args.kwargs.get("env", {})
    assert env.get("LD_LIBRARY_PATH") == original_ld


@patch("cyberwave_cli.device_utils.shutil.which", return_value="/usr/bin/v4l2-ctl")
@patch("cyberwave_cli.device_utils._ensure_video_device_permissions")
@patch("cyberwave_cli.device_utils.subprocess.run")
def test_discover_v4l2_no_pyinstaller_passes_env_unchanged(mock_run, _mock_perms, _mock_which):
    """When NOT in a PyInstaller bundle, LD_LIBRARY_PATH passes through as-is."""
    mock_run.return_value = MagicMock(
        returncode=0, stdout="", stderr="",
    )

    normal_ld = "/usr/lib/aarch64-linux-gnu"

    with patch.dict(os.environ, {"LD_LIBRARY_PATH": normal_ld}, clear=False):
        if hasattr(sys, "_MEIPASS"):
            delattr(sys, "_MEIPASS")
        os.environ.pop("LD_LIBRARY_PATH_ORIG", None)
        discover_usb_cameras_v4l2()

    env = mock_run.call_args.kwargs.get("env", {})
    assert env.get("LD_LIBRARY_PATH") == normal_ld


# ---------------------------------------------------------------------------
# camera_likelihood_score
# ---------------------------------------------------------------------------

def test_camera_score_usb_webcam_high():
    cam = CameraDevice(card="HD Pro Webcam C920", bus_info="usb-0000:01:00.0-1.2",
                       paths=["/dev/video0"], driver="uvcvideo")
    assert camera_likelihood_score(cam) >= 70


def test_camera_score_codec_low():
    cam = CameraDevice(card="bcm2835-codec-decode", bus_info="platform:bcm2835-codec",
                       paths=["/dev/video10"])
    assert camera_likelihood_score(cam) < 40


def test_camera_score_isp_low():
    cam = CameraDevice(card="bcm2835-isp", bus_info="platform:bcm2835-isp",
                       paths=["/dev/video13"])
    assert camera_likelihood_score(cam) < 40


def test_camera_score_rpivid_low():
    cam = CameraDevice(card="rpivid", bus_info="platform:rpivid",
                       paths=["/dev/video19"])
    assert camera_likelihood_score(cam) < 40


def test_camera_score_generic_usb():
    """A USB device with no special keywords still scores higher than platform devices."""
    cam = CameraDevice(card="Unknown USB Device", bus_info="usb-0000:01:00.0-1.3",
                       paths=["/dev/video4"])
    assert camera_likelihood_score(cam) >= 50


# ---------------------------------------------------------------------------
# Exclusion and sorting integration
# ---------------------------------------------------------------------------

@patch("cyberwave_cli.device_utils.shutil.which", return_value="/usr/bin/v4l2-ctl")
@patch("cyberwave_cli.device_utils._ensure_video_device_permissions")
@patch("cyberwave_cli.device_utils._get_v4l2_device_info", return_value={})
@patch("cyberwave_cli.device_utils.subprocess.run")
def test_discover_v4l2_excludes_broadcom_platform_devices(mock_run, _info, _perms, _which):
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=(
            "bcm2835-codec-decode (platform:bcm2835-codec):\n"
            "\t/dev/video10\n"
            "\n"
            "HD Pro Webcam C920 (usb-0000:01:00.0-1.2):\n"
            "\t/dev/video0\n"
        ),
        stderr="",
    )
    devices = discover_usb_cameras_v4l2()
    assert len(devices) == 1
    assert devices[0].card == "HD Pro Webcam C920"


@patch("cyberwave_cli.device_utils.discover_usb_cameras_v4l2")
def test_discover_sorts_real_cameras_first(mock_v4l2):
    """Real cameras should appear before platform devices in the returned list."""
    mock_v4l2.return_value = [
        CameraDevice(card="SomeCodec", bus_info="platform:codec", paths=["/dev/video10"]),
        CameraDevice(card="HD Pro Webcam C920", bus_info="usb-0000:01:00.0-1.2",
                     paths=["/dev/video0"], driver="uvcvideo"),
    ]
    devices = discover_usb_cameras()
    assert len(devices) == 2
    assert devices[0].card == "HD Pro Webcam C920"


# ---------------------------------------------------------------------------
# write_cameras_json — twin-to-device mapping
# ---------------------------------------------------------------------------

import json  # noqa: E402

from cyberwave_cli.device_utils import write_cameras_json  # noqa: E402


def test_write_cameras_json_without_mapping(tmp_path):
    cams = [
        CameraDevice(card="Cam A", bus_info="usb-1", paths=["/dev/video0"]),
        CameraDevice(card="Cam B", bus_info="usb-2", paths=["/dev/video2"]),
    ]
    out = write_cameras_json(cams, tmp_path, selected_index=2)
    data = json.loads(out.read_text())
    assert data["selected_device"] == 2
    assert "twin_to_device" not in data
    assert len(data["devices"]) == 2


def test_write_cameras_json_persists_twin_mapping(tmp_path):
    cams = [
        CameraDevice(card="Cam A", bus_info="usb-1", paths=["/dev/video0"]),
        CameraDevice(card="Cam B", bus_info="usb-2", paths=["/dev/video2"]),
    ]
    mapping = {"twin-a": 0, "twin-b": 2}
    out = write_cameras_json(
        cams,
        tmp_path,
        selected_index=0,
        twin_to_device=mapping,
    )
    data = json.loads(out.read_text())
    assert data["selected_device"] == 0
    assert data["twin_to_device"] == {"twin-a": 0, "twin-b": 2}


def test_write_cameras_json_coerces_mapping_values(tmp_path):
    """Non-int values in the mapping are coerced to plain ints for JSON stability."""
    cams = [CameraDevice(card="Cam A", bus_info="usb-1", paths=["/dev/video0"])]
    out = write_cameras_json(
        cams,
        tmp_path,
        selected_index=0,
        twin_to_device={"twin-a": "0"},  # type: ignore[dict-item]
    )
    data = json.loads(out.read_text())
    assert data["twin_to_device"] == {"twin-a": 0}
