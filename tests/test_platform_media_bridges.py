"""Per-platform contracts for host media bridges (camera / microphone / USB/IP).

These tests document what ``cyberwave edge install`` automates on each OS.
Darwin-only cases are skipped on Linux CI; Linux-only cases are skipped on macOS.
"""

from __future__ import annotations

import json
import platform
import sys

import pytest

from tests._core_module_loader import load_core_module

pytestmark = pytest.mark.filterwarnings("ignore")


def _mock_is_macos(monkeypatch, core, value: bool):
    import cyberwave_cli.macos as macos_mod

    monkeypatch.setattr(core, "_is_macos", lambda: value, raising=False)
    monkeypatch.setattr(core, "is_macos", lambda: value)
    monkeypatch.setattr(macos_mod, "is_macos", lambda: value)


# ---------------------------------------------------------------------------
# Install orchestration (mocked helpers)
# ---------------------------------------------------------------------------


@pytest.mark.darwin
def test_macos_install_automates_usbip_camera_and_audio(monkeypatch):
    """On macOS + Docker, edge install must call all three host bridge installers."""
    if platform.system() != "Darwin":
        pytest.skip("macOS-only contract")

    core = load_core_module(monkeypatch)
    calls: list[str] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    _mock_is_macos(monkeypatch, core, True)
    monkeypatch.setattr(core, "_check_docker_macos", lambda: True)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "_any_twin_has_camera_sensor", lambda: True)
    monkeypatch.setattr(core, "_any_twin_has_microphone_sensor", lambda: True)
    monkeypatch.setattr(
        core, "install_service_package", lambda spec, *, channel, version: True
    )
    monkeypatch.setattr(
        core, "setup_usbip_server", lambda **kw: calls.append("usbip") or True
    )
    monkeypatch.setattr(
        core,
        "setup_camera_stream_server",
        lambda **kw: calls.append("camera") or True,
    )
    monkeypatch.setattr(
        core,
        "setup_audio_stream_server",
        lambda **kw: calls.append("audio") or True,
    )
    monkeypatch.setattr(core, "configure_edge_environment", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "create_launchagent_service", lambda *a, **kw: True)
    monkeypatch.setattr(core, "load_launchagent_service", lambda spec: True)
    monkeypatch.setattr(core, "_list_camera_twins", lambda: [])
    monkeypatch.setattr(core, "_list_microphone_twins", lambda: [])

    assert core.setup_edge_core(skip_confirm=True) is True
    assert calls == ["usbip", "camera", "audio"]


@pytest.mark.linux
def test_linux_install_skips_macos_bridges_and_uses_v4l2(monkeypatch):
    """Linux install must not call macOS ffmpeg bridges; camera uses cameras.json."""
    core = load_core_module(monkeypatch)
    calls: list[str] = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    _mock_is_macos(monkeypatch, core, False)
    monkeypatch.setattr(core.os, "geteuid", lambda: 0)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "_any_twin_has_camera_sensor", lambda: True)
    monkeypatch.setattr(core, "_any_twin_has_microphone_sensor", lambda: True)
    monkeypatch.setattr(
        core, "install_service_package", lambda spec, *, channel, version: True
    )
    monkeypatch.setattr(core, "_install_docker", lambda: True)
    monkeypatch.setattr(
        core,
        "setup_usbip_server",
        lambda **kw: (_ for _ in ()).throw(AssertionError("usbip on linux")),
    )
    monkeypatch.setattr(
        core,
        "setup_camera_stream_server",
        lambda **kw: (_ for _ in ()).throw(AssertionError("mjpeg on linux")),
    )
    monkeypatch.setattr(
        core,
        "setup_audio_stream_server",
        lambda **kw: (_ for _ in ()).throw(AssertionError("audio bridge on linux")),
    )
    monkeypatch.setattr(
        core,
        "_detect_and_select_cameras",
        lambda: calls.append("v4l2") or None,
    )
    monkeypatch.setattr(core, "configure_edge_environment", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "create_systemd_service", lambda spec=None: True)
    monkeypatch.setattr(core, "enable_and_start_service", lambda spec=None: True)

    assert core.setup_edge_core(skip_confirm=True) is True
    assert calls == ["v4l2"]


def test_macos_install_skips_camera_when_no_camera_twins(monkeypatch):
    core = load_core_module(monkeypatch)
    calls: list[str] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    _mock_is_macos(monkeypatch, core, True)
    monkeypatch.setattr(core, "_check_docker_macos", lambda: True)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "_any_twin_has_camera_sensor", lambda: False)
    monkeypatch.setattr(core, "_any_twin_has_microphone_sensor", lambda: False)
    monkeypatch.setattr(
        core, "install_service_package", lambda spec, *, channel, version: True
    )
    monkeypatch.setattr(
        core, "setup_usbip_server", lambda **kw: calls.append("usbip") or True
    )
    monkeypatch.setattr(
        core,
        "setup_camera_stream_server",
        lambda **kw: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        core,
        "setup_audio_stream_server",
        lambda **kw: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(core, "configure_edge_environment", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "create_launchagent_service", lambda *a, **kw: True)
    monkeypatch.setattr(core, "load_launchagent_service", lambda spec: True)

    assert core.setup_edge_core(skip_confirm=True) is True
    assert calls == ["usbip"]


# ---------------------------------------------------------------------------
# macOS helper unit tests (no launchd)
# ---------------------------------------------------------------------------


def _load_macos(monkeypatch):
    load_core_module(monkeypatch)
    import cyberwave_cli.macos as macos_mod

    return macos_mod


def test_avfoundation_audio_device_spec_uses_none_prefix(monkeypatch):
    macos = _load_macos(monkeypatch)
    assert macos._avfoundation_audio_device_spec("MacBook Microphone") == (
        "none:MacBook Microphone"
    )
    assert macos._avfoundation_audio_device_spec("none:0") == "none:0"


def test_resolve_microphone_capture_settings_reads_twin_parameters(
    monkeypatch, tmp_path
):
    macos = _load_macos(monkeypatch)
    import cyberwave_cli.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    (tmp_path / "twin-mic.json").write_text(
        json.dumps(
            {
                "uuid": "twin-mic",
                "metadata": {
                    "sensors": [
                        {
                            "type": "audio",
                            "parameters": {
                                "audio_sample_rate": "32000",
                                "audio_channels": "2",
                            },
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    rate, channels = macos._resolve_microphone_capture_settings(
        [("twin-mic", "Mic")]
    )
    assert rate == 32000
    assert channels == 2


def test_setup_audio_stream_single_twin_writes_mapping(monkeypatch, tmp_path):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "is_audio_stream_running", lambda: False)
    monkeypatch.setattr(macos, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(
        macos, "_list_avfoundation_audio_devices", lambda: [(0, "Built-in Mic")]
    )
    import cyberwave_cli.config as config_mod

    monkeypatch.setattr(config_mod, "CONFIG_DIR", tmp_path)
    (tmp_path / "twin-a.json").write_text(
        json.dumps(
            {
                "uuid": "twin-a",
                "asset": {
                    "universal_schema": {
                        "sensors": [
                            {
                                "type": "audio",
                                "parameters": {
                                    "audio_sample_rate": "32000",
                                    "audio_channels": "2",
                                },
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    bring_up_calls: list[dict] = []

    def _fake_bring_up(*, slot, device_name, sample_rate=48_000, channels=1):
        bring_up_calls.append(
            {
                "slot": slot,
                "device_name": device_name,
                "sample_rate": sample_rate,
                "channels": channels,
            }
        )
        return True, True

    monkeypatch.setattr(macos, "_bring_up_audio_stream_slot", _fake_bring_up)

    streams_file = tmp_path / "audio_streams.json"
    monkeypatch.setattr(macos, "_audio_streams_config_path", lambda: streams_file)
    monkeypatch.setattr(
        macos,
        "_write_file_as_real_user",
        lambda path, contents, **kw: streams_file.write_text(contents),
    )

    import cyberwave_cli.credentials as credentials_mod

    monkeypatch.setattr(
        credentials_mod, "upsert_runtime_env", lambda *_a, **_kw: None, raising=False
    )

    assert (
        macos.setup_audio_stream_server(microphone_twins=[("twin-a", "Mic A")]) is True
    )
    assert bring_up_calls == [
        {
            "slot": 0,
            "device_name": "Built-in Mic",
            "sample_rate": 32000,
            "channels": 2,
        }
    ]
    data = json.loads(streams_file.read_text())
    assert data["twin_to_stream_url"] == {
        "twin-a": f"http://host.docker.internal:{macos.AUDIO_STREAM_PORT}"
    }
    assert data["capture_sample_rate"] == 32000
    assert data["channels"] == 2


@pytest.mark.darwin
def test_list_avfoundation_audio_devices_parses_ffmpeg_stderr(monkeypatch):
    """Integration: requires ffmpeg on PATH (macOS dev machines)."""
    if platform.system() != "Darwin":
        pytest.skip("requires macOS + ffmpeg")
    macos = _load_macos(monkeypatch)
    if not macos._has_ffmpeg():
        pytest.skip("ffmpeg not installed")

    devices = macos._list_avfoundation_audio_devices()
    assert isinstance(devices, list)
    # Most Macs expose at least one input; absence is not a test failure.
    for idx, name in devices:
        assert isinstance(idx, int)
        assert isinstance(name, str) and name


def pytest_configure(config):
    config.addinivalue_line("markers", "darwin: requires Darwin platform")
    config.addinivalue_line("markers", "linux: requires Linux platform")
