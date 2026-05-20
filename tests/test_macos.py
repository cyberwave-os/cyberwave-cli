"""Tests for cyberwave_cli.macos — USB/IP server setup on macOS.

Detection-logic tests for ``is_usbip_server_running`` and ``is_port_listening``
live in the SDK test suite (``tests/test_edge_platform.py``) since the
implementation now lives in ``cyberwave.edge.platform``.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests._core_module_loader import load_core_module


def _load_macos(monkeypatch):
    """Import macos module with stubs already in place from the loader."""
    load_core_module(monkeypatch)
    import cyberwave_cli.macos as macos_mod

    return macos_mod


# ---- is_macos / helpers ------------------------------------------------------


def test_is_macos_returns_true_on_darwin(monkeypatch):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos.platform, "system", lambda: "Darwin")
    assert macos.is_macos() is True


def test_is_macos_returns_false_on_linux(monkeypatch):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos.platform, "system", lambda: "Linux")
    assert macos.is_macos() is False


# ---- path resolution under sudo ---------------------------------------------


def test_paths_resolve_via_sudo_user_home(monkeypatch):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "_resolve_sudo_user_home", lambda: Path("/Users/alice"))

    assert macos._user_home() == Path("/Users/alice")
    assert macos._usbip_install_dir() == Path("/Users/alice/.cyberwave/usbip")
    assert "alice" in str(macos._usbip_launchd_plist())
    assert "alice" in str(macos._usbip_log_path())
    assert "alice" in str(macos._usbip_wrapper_path())


def test_paths_fall_back_to_home_without_sudo(monkeypatch):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "_resolve_sudo_user_home", lambda: None)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: Path("/Users/bob")))

    assert macos._user_home() == Path("/Users/bob")
    assert macos._usbip_install_dir() == Path("/Users/bob/.cyberwave/usbip")


# ---- _install_usbip_server --------------------------------------------------


def test_install_skips_when_already_built(monkeypatch):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "_has_git", lambda: True)
    monkeypatch.setattr(macos, "_has_cargo", lambda: True)
    monkeypatch.setattr(macos, "_usbip_binary_path", lambda: Path("/fake/host"))
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    assert macos._install_usbip_server() is True


def test_install_fails_without_git(monkeypatch):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "_has_git", lambda: False)
    monkeypatch.setattr(macos, "_has_cargo", lambda: True)

    assert macos._install_usbip_server() is False


def test_install_usbip_invokes_rust_auto_install_when_cargo_missing(monkeypatch):
    """A missing cargo must trigger the new rustup auto-install path,
    not abort with a hint like the legacy behavior did. The
    skip_confirm flag must propagate through so ``cyberwave edge install -y``
    skips the rustup confirmation as well."""
    macos = _load_macos(monkeypatch)

    monkeypatch.setattr(macos, "_has_git", lambda: True)
    monkeypatch.setattr(macos, "_has_cargo", lambda: False)

    rust_install_calls: list[dict] = []

    def fake_rust_install(*, skip_confirm: bool = False):
        rust_install_calls.append({"skip_confirm": skip_confirm})
        return True

    monkeypatch.setattr(macos, "_install_rust_toolchain", fake_rust_install)
    monkeypatch.setattr(macos, "_usbip_binary_path", lambda: Path("/fake/host"))
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    assert macos._install_usbip_server(skip_confirm=True) is True
    assert rust_install_calls == [{"skip_confirm": True}], (
        "Missing cargo must invoke _install_rust_toolchain with the same "
        "skip_confirm flag the caller passed in."
    )


def test_install_fails_when_rust_auto_install_fails(monkeypatch):
    """If rustup auto-install returns False (user declined, network
    error, rustup crashed, etc.), _install_usbip_server must abort
    instead of trying to invoke cargo build with no toolchain."""
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "_has_git", lambda: True)
    monkeypatch.setattr(macos, "_has_cargo", lambda: False)
    monkeypatch.setattr(
        macos, "_install_rust_toolchain", lambda **_kw: False
    )

    assert macos._install_usbip_server() is False


# ---- setup_usbip_server (integration) ----------------------------------------


def test_setup_skips_on_non_macos(monkeypatch):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: False)
    assert macos.setup_usbip_server() is True


def test_setup_short_circuits_if_already_running(monkeypatch):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "is_usbip_server_running", lambda: True)

    assert macos.setup_usbip_server() is True


def test_setup_returns_false_when_install_fails(monkeypatch):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "is_usbip_server_running", lambda: False)
    monkeypatch.setattr(macos, "_install_usbip_server", lambda **_kw: False)

    assert macos.setup_usbip_server() is False


# ---- teardown ----------------------------------------------------------------


def test_teardown_usbip_removes_service_artifacts(monkeypatch, tmp_path):
    macos = _load_macos(monkeypatch)

    plist = tmp_path / "com.cyberwave.usbip.plist"
    wrapper = tmp_path / "usbip_wrapper.sh"
    log = tmp_path / "usbip.log"
    install_dir = tmp_path / "usbip"
    install_dir.mkdir()
    (install_dir / "binary").write_text("keep me")
    for f in (plist, wrapper, log):
        f.write_text("test")

    monkeypatch.setattr(macos, "_usbip_launchd_plist", lambda: plist)
    monkeypatch.setattr(macos, "_usbip_wrapper_path", lambda: wrapper)
    monkeypatch.setattr(macos, "_usbip_log_path", lambda: log)
    monkeypatch.setattr(macos, "_bootout_launchd_service", lambda label: None)

    macos._teardown_usbip_server()

    assert not plist.exists()
    assert not wrapper.exists()
    assert not log.exists()
    assert install_dir.exists(), "teardown must preserve the compiled binary tree"


def test_teardown_camera_stream_removes_artifacts(monkeypatch, tmp_path):
    macos = _load_macos(monkeypatch)

    plist = tmp_path / "com.cyberwave.camera-stream.plist"
    wrapper = tmp_path / "camera_stream.sh"
    log = tmp_path / "camera_stream.log"
    streams_config = tmp_path / "camera_streams.json"
    for f in (plist, wrapper, log, streams_config):
        f.write_text("test")

    monkeypatch.setattr(macos, "_camera_stream_plist_path", lambda slot=None: plist)
    monkeypatch.setattr(macos, "_camera_stream_wrapper_path", lambda slot=None: wrapper)
    monkeypatch.setattr(macos, "_camera_stream_log_path", lambda slot=None: log)
    monkeypatch.setattr(macos, "_camera_streams_config_path", lambda: streams_config)
    monkeypatch.setattr(macos, "_discover_camera_stream_slots", lambda: [0])
    monkeypatch.setattr(macos, "_bootout_launchd_service", lambda label: None)

    macos._teardown_camera_stream_server()

    assert not plist.exists()
    assert not wrapper.exists()
    assert not log.exists()
    assert not streams_config.exists()


# ---- force reinstall ---------------------------------------------------------


def test_setup_usbip_force_calls_teardown_then_installs(monkeypatch):
    macos = _load_macos(monkeypatch)
    calls: list[str] = []

    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(
        macos, "_teardown_usbip_server", lambda: calls.append("teardown")
    )
    monkeypatch.setattr(
        macos,
        "_install_usbip_server",
        lambda **_kw: calls.append("install") or True,
    )
    monkeypatch.setattr(
        macos, "_create_usbip_launchd_service", lambda: calls.append("launchd") or True
    )

    assert macos.setup_usbip_server(force=True) is True
    assert calls == ["teardown", "install", "launchd"]


def test_setup_usbip_force_skips_running_check(monkeypatch):
    macos = _load_macos(monkeypatch)

    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "is_usbip_server_running", lambda: True)
    monkeypatch.setattr(macos, "_teardown_usbip_server", lambda: None)
    monkeypatch.setattr(macos, "_install_usbip_server", lambda **_kw: True)
    monkeypatch.setattr(macos, "_create_usbip_launchd_service", lambda: True)

    assert macos.setup_usbip_server(force=True) is True


def test_setup_camera_stream_force_calls_teardown(monkeypatch):
    macos = _load_macos(monkeypatch)
    calls: list[str] = []

    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(
        macos, "_teardown_camera_stream_server", lambda: calls.append("teardown")
    )
    monkeypatch.setattr(macos, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(
        macos, "_camera_stream_wrapper_path", lambda slot=None: MagicMock()
    )
    monkeypatch.setattr(
        macos, "_camera_stream_plist_path", lambda slot=None: MagicMock()
    )
    monkeypatch.setattr(
        macos, "_camera_stream_log_path", lambda slot=None: MagicMock()
    )
    monkeypatch.setattr(macos, "_chown_to_real_user", lambda *a, **kw: None)
    monkeypatch.setattr(macos, "_write_file_as_real_user", lambda *a, **kw: None)
    monkeypatch.setattr(macos, "_resolve_real_user", lambda: ("user", 501, 20))
    monkeypatch.setattr(macos, "_bootout_launchd_service", lambda *a, **kw: None)
    monkeypatch.setattr(macos, "_run", lambda *a, **kw: MagicMock())
    monkeypatch.setattr(macos, "_list_avfoundation_devices", lambda: [(0, "FaceTime")])

    macos.setup_camera_stream_server(force=True)
    assert "teardown" in calls


# ---- per-twin camera stream mapping -----------------------------------------


def _patch_bring_up_recorder(macos, monkeypatch, *, results=None):
    """Record per-slot bring-up calls and report (loaded, port_open) per call.

    *results*, when given, is a list of ``(loaded, port_open)`` tuples consumed
    in order; otherwise every call reports ``(True, True)`` (fully healthy).
    """
    calls: list[dict] = []
    iterator = iter(results or [])

    def _fake_bring_up(*, slot, device_name):
        calls.append({"slot": slot, "device_name": device_name})
        return next(iterator, (True, True))

    monkeypatch.setattr(macos, "_bring_up_camera_stream_slot", _fake_bring_up)
    return calls


def test_setup_camera_stream_single_twin_writes_mapping(monkeypatch, tmp_path):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "is_camera_stream_running", lambda: False)
    monkeypatch.setattr(macos, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(
        macos, "_list_avfoundation_devices", lambda: [(0, "FaceTime")]
    )
    bring_up_calls = _patch_bring_up_recorder(macos, monkeypatch)

    streams_file = tmp_path / "camera_streams.json"
    monkeypatch.setattr(macos, "_camera_streams_config_path", lambda: streams_file)
    monkeypatch.setattr(
        macos,
        "_write_file_as_real_user",
        lambda path, contents, **kw: streams_file.write_text(contents),
    )

    import cyberwave_cli.credentials as credentials_mod

    monkeypatch.setattr(
        credentials_mod,
        "upsert_runtime_env",
        lambda *_a, **_kw: None,
        raising=False,
    )

    assert (
        macos.setup_camera_stream_server(camera_twins=[("twin-a", "Cam A")]) is True
    )

    assert bring_up_calls == [{"slot": 0, "device_name": "FaceTime"}]
    import json

    data = json.loads(streams_file.read_text())
    assert data["twin_to_stream_url"] == {
        "twin-a": f"http://host.docker.internal:{macos.CAMERA_STREAM_PORT}"
    }


def test_setup_camera_stream_multi_twin_allocates_slots(monkeypatch, tmp_path):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "is_camera_stream_running", lambda: False)
    monkeypatch.setattr(macos, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(
        macos,
        "_list_avfoundation_devices",
        lambda: [(0, "FaceTime"), (1, "USB Cam")],
    )
    bring_up_calls = _patch_bring_up_recorder(macos, monkeypatch)

    streams_file = tmp_path / "camera_streams.json"
    monkeypatch.setattr(macos, "_camera_streams_config_path", lambda: streams_file)
    monkeypatch.setattr(
        macos,
        "_write_file_as_real_user",
        lambda path, contents, **kw: streams_file.write_text(contents),
    )

    import cyberwave_cli.credentials as credentials_mod

    monkeypatch.setattr(
        credentials_mod,
        "upsert_runtime_env",
        lambda *_a, **_kw: None,
        raising=False,
    )

    # First twin picks camera 0, second twin picks camera 1.
    inputs = iter(["0", "1"])
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(inputs))

    assert (
        macos.setup_camera_stream_server(
            camera_twins=[("twin-a", "Cam A"), ("twin-b", "Cam B")],
        )
        is True
    )

    # Distinct cameras → distinct slots.  Slot 0 uses the legacy port, slot 1
    # uses the next port.
    assert bring_up_calls == [
        {"slot": 0, "device_name": "FaceTime"},
        {"slot": 1, "device_name": "USB Cam"},
    ]

    import json

    data = json.loads(streams_file.read_text())
    port0 = macos.CAMERA_STREAM_PORT
    port1 = macos.CAMERA_STREAM_PORT + 1
    assert data["twin_to_stream_url"] == {
        "twin-a": f"http://host.docker.internal:{port0}",
        "twin-b": f"http://host.docker.internal:{port1}",
    }


def test_setup_camera_stream_multi_twin_shared_camera(monkeypatch, tmp_path):
    """Two twins pointing at the same physical camera should reuse a single slot."""
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "is_camera_stream_running", lambda: False)
    monkeypatch.setattr(macos, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(
        macos,
        "_list_avfoundation_devices",
        lambda: [(0, "FaceTime"), (1, "USB Cam")],
    )
    bring_up_calls = _patch_bring_up_recorder(macos, monkeypatch)

    streams_file = tmp_path / "camera_streams.json"
    monkeypatch.setattr(macos, "_camera_streams_config_path", lambda: streams_file)
    monkeypatch.setattr(
        macos,
        "_write_file_as_real_user",
        lambda path, contents, **kw: streams_file.write_text(contents),
    )

    import cyberwave_cli.credentials as credentials_mod

    monkeypatch.setattr(
        credentials_mod,
        "upsert_runtime_env",
        lambda *_a, **_kw: None,
        raising=False,
    )

    inputs = iter(["1", "1"])
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(inputs))

    assert (
        macos.setup_camera_stream_server(
            camera_twins=[("twin-a", "Cam A"), ("twin-b", "Cam B")],
        )
        is True
    )

    # Only one bring-up despite two twins.
    assert bring_up_calls == [{"slot": 0, "device_name": "USB Cam"}]

    import json

    data = json.loads(streams_file.read_text())
    expected_url = f"http://host.docker.internal:{macos.CAMERA_STREAM_PORT}"
    assert data["twin_to_stream_url"] == {
        "twin-a": expected_url,
        "twin-b": expected_url,
    }


def _make_multi_twin_harness(monkeypatch, tmp_path, *, inputs, bring_up_results=None):
    """Common scaffolding for the multi-twin flow tests."""
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "is_camera_stream_running", lambda: False)
    monkeypatch.setattr(macos, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(
        macos,
        "_list_avfoundation_devices",
        lambda: [(0, "FaceTime"), (1, "USB Cam")],
    )
    bring_up_calls = _patch_bring_up_recorder(
        macos, monkeypatch, results=bring_up_results
    )

    streams_file = tmp_path / "camera_streams.json"
    monkeypatch.setattr(macos, "_camera_streams_config_path", lambda: streams_file)
    monkeypatch.setattr(
        macos,
        "_write_file_as_real_user",
        lambda path, contents, **kw: streams_file.write_text(contents),
    )

    import cyberwave_cli.credentials as credentials_mod

    monkeypatch.setattr(
        credentials_mod,
        "upsert_runtime_env",
        lambda *_a, **_kw: None,
        raising=False,
    )

    iterator = iter(inputs)
    monkeypatch.setattr("builtins.input", lambda *_a, **_kw: next(iterator))

    return macos, streams_file, bring_up_calls


def test_multi_mode_persists_partial_map_on_invalid_input(monkeypatch, tmp_path):
    """User fat-fingers camera index for twin B — twin A's mapping must survive."""
    macos, streams_file, _calls = _make_multi_twin_harness(
        monkeypatch,
        tmp_path,
        inputs=["0", "not-a-number"],
    )

    assert (
        macos.setup_camera_stream_server(
            camera_twins=[("twin-a", "Cam A"), ("twin-b", "Cam B")],
        )
        is True
    )

    import json

    data = json.loads(streams_file.read_text())
    assert data["twin_to_stream_url"] == {
        "twin-a": f"http://host.docker.internal:{macos.CAMERA_STREAM_PORT}"
    }


def test_multi_mode_persists_partial_map_on_ctrl_c(monkeypatch, tmp_path):
    def _raising_input(*_a, **_kw):
        raise KeyboardInterrupt

    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "is_camera_stream_running", lambda: False)
    monkeypatch.setattr(macos, "_has_ffmpeg", lambda: True)
    monkeypatch.setattr(
        macos,
        "_list_avfoundation_devices",
        lambda: [(0, "FaceTime"), (1, "USB Cam")],
    )
    _patch_bring_up_recorder(macos, monkeypatch)

    streams_file = tmp_path / "camera_streams.json"
    monkeypatch.setattr(macos, "_camera_streams_config_path", lambda: streams_file)
    monkeypatch.setattr(
        macos,
        "_write_file_as_real_user",
        lambda path, contents, **kw: streams_file.write_text(contents),
    )

    import cyberwave_cli.credentials as credentials_mod

    monkeypatch.setattr(
        credentials_mod,
        "upsert_runtime_env",
        lambda *_a, **_kw: None,
        raising=False,
    )

    monkeypatch.setattr("builtins.input", _raising_input)

    # Ctrl-C before any twin is mapped → nothing persisted, returns False.
    assert (
        macos.setup_camera_stream_server(
            camera_twins=[("twin-a", "Cam A"), ("twin-b", "Cam B")],
        )
        is False
    )
    assert not streams_file.exists()


def test_multi_mode_drops_twins_whose_slot_failed_to_register(monkeypatch, tmp_path):
    """When a slot can't be registered with launchd, its twin URL must be omitted."""
    # First slot registers cleanly, second slot fails to register (loaded=False).
    macos, streams_file, _calls = _make_multi_twin_harness(
        monkeypatch,
        tmp_path,
        inputs=["0", "1"],
        bring_up_results=[(True, True), (False, False)],
    )

    macos.setup_camera_stream_server(
        camera_twins=[("twin-a", "Cam A"), ("twin-b", "Cam B")],
    )

    import json

    data = json.loads(streams_file.read_text())
    # twin-b's slot failed → omitted so edge-core falls back to the env var.
    assert data["twin_to_stream_url"] == {
        "twin-a": f"http://host.docker.internal:{macos.CAMERA_STREAM_PORT}",
    }


def test_multi_mode_persists_map_for_pending_port(monkeypatch, tmp_path):
    """Loaded plist + port not yet listening → still persist; launchd will retry."""
    macos, streams_file, _calls = _make_multi_twin_harness(
        monkeypatch,
        tmp_path,
        inputs=["0", "1"],
        bring_up_results=[(True, True), (True, False)],
    )

    assert (
        macos.setup_camera_stream_server(
            camera_twins=[("twin-a", "Cam A"), ("twin-b", "Cam B")],
        )
        is True
    )

    import json

    data = json.loads(streams_file.read_text())
    assert data["twin_to_stream_url"] == {
        "twin-a": f"http://host.docker.internal:{macos.CAMERA_STREAM_PORT}",
        "twin-b": f"http://host.docker.internal:{macos.CAMERA_STREAM_PORT + 1}",
    }


def test_multi_mode_returns_false_when_every_slot_fails(monkeypatch, tmp_path):
    macos, streams_file, _calls = _make_multi_twin_harness(
        monkeypatch,
        tmp_path,
        inputs=["0", "1"],
        bring_up_results=[(False, False), (False, False)],
    )

    assert (
        macos.setup_camera_stream_server(
            camera_twins=[("twin-a", "Cam A"), ("twin-b", "Cam B")],
        )
        is False
    )


def test_discover_camera_stream_slots_finds_numbered_plists(monkeypatch, tmp_path):
    macos = _load_macos(monkeypatch)
    agents_dir = tmp_path / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True)
    (agents_dir / f"{macos.CAMERA_STREAM_LAUNCHD_LABEL}.plist").write_text("x")
    (agents_dir / f"{macos.CAMERA_STREAM_LAUNCHD_LABEL_PREFIX}1.plist").write_text("x")
    (agents_dir / f"{macos.CAMERA_STREAM_LAUNCHD_LABEL_PREFIX}2.plist").write_text("x")
    (agents_dir / "com.example.other.plist").write_text("x")

    monkeypatch.setattr(macos, "_user_home", lambda: tmp_path)

    assert macos._discover_camera_stream_slots() == [0, 1, 2]


# ---- init_console ------------------------------------------------------------


def test_init_console_injects_shared_instance(monkeypatch):
    macos = _load_macos(monkeypatch)
    mock_console = MagicMock()
    macos.init_console(mock_console)
    assert macos._get_console() is mock_console


# ---- launchd timing helpers --------------------------------------------------


def _stub_real_user(macos, monkeypatch, *, uid=501):
    monkeypatch.setattr(
        macos, "_resolve_real_user", lambda: (f"user{uid}", uid, 20)
    )


def test_wait_for_launchd_unload_returns_true_when_label_never_loaded(
    monkeypatch,
):
    """If launchctl print never returns 0, the label is already gone."""
    macos = _load_macos(monkeypatch)
    _stub_real_user(macos, monkeypatch)

    calls: list[list[str]] = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if "bootout" in cmd:
            return SimpleNamespace(returncode=113, stdout=b"", stderr=b"")
        return SimpleNamespace(returncode=64, stdout=b"", stderr=b"")

    monkeypatch.setattr(macos.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)

    assert macos.wait_for_launchd_unload("com.example.svc", timeout=2.0) is True

    bootout_targets = [
        cmd[cmd.index("bootout") + 1] for cmd in calls if "bootout" in cmd
    ]
    assert "gui/501/com.example.svc" in bootout_targets
    assert "system/com.example.svc" in bootout_targets


def test_wait_for_launchd_unload_bootouts_legacy_labels_too(monkeypatch):
    """legacy_labels are booted out alongside the primary label so users
    upgrading from older CLI builds don't end up with two LaunchAgents
    running the same logical service under different labels."""
    macos = _load_macos(monkeypatch)
    _stub_real_user(macos, monkeypatch)

    bootout_targets: list[str] = []

    def fake_subprocess_run(cmd, *args, **kwargs):
        if "bootout" in cmd:
            bootout_targets.append(cmd[cmd.index("bootout") + 1])
            return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
        return SimpleNamespace(returncode=64, stdout=b"", stderr=b"")

    monkeypatch.setattr(macos.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)

    assert (
        macos.wait_for_launchd_unload(
            "com.cyberwave.edge.core",
            timeout=2.0,
            legacy_labels=("com.cyberwave.edge-core",),
        )
        is True
    )

    # Both primary and legacy labels must be booted out in both domains.
    assert "gui/501/com.cyberwave.edge.core" in bootout_targets
    assert "system/com.cyberwave.edge.core" in bootout_targets
    assert "gui/501/com.cyberwave.edge-core" in bootout_targets
    assert "system/com.cyberwave.edge-core" in bootout_targets


def test_legacy_labels_for_package_returns_edge_core_history(monkeypatch):
    macos = _load_macos(monkeypatch)
    assert macos.legacy_labels_for_package("cyberwave-edge-core") == (
        "com.cyberwave.edge-core",
    )
    assert macos.legacy_labels_for_package("cyberwave-cloud-node") == ()
    assert macos.legacy_labels_for_package("unrelated") == ()


def _executable_lines(rendered: str) -> list[str]:
    """Strip comments and blank lines so the structural assertions below
    don't trip over comment text that happens to contain the very tokens
    we're forbidding (e.g. the comment explaining *why* ``set -e`` is
    intentionally absent)."""
    result: list[str] = []
    for raw in rendered.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        result.append(raw)
    return result


def test_camera_stream_wrapper_template_uses_persistent_loop(monkeypatch):
    """The wrapper script must loop ffmpeg in bash so that launchd's
    spawn-throttle never disables the service.  ``-listen 1`` is one-shot
    by design, so without an outer loop the agent gets removed after a
    short burst of consumer reconnects."""
    macos = _load_macos(monkeypatch)
    rendered = macos._CAMERA_STREAM_WRAPPER_TEMPLATE.format(port=8091)
    code_only = "\n".join(_executable_lines(rendered))

    assert "while true; do" in code_only
    assert "sleep 1" in code_only, (
        "Need a small backoff between restarts to avoid tight spinning"
    )
    assert "exec ffmpeg" not in code_only, (
        "exec would replace bash with ffmpeg, defeating the outer loop"
    )
    assert "trap 'exit 0' INT TERM" in code_only, (
        "launchctl bootout must cleanly stop the wrapper"
    )
    # ``set -e`` would propagate ffmpeg's non-zero exit, collapsing the
    # outer loop after the first restart.  Pin its absence in executable
    # code (the surrounding comment text is allowed to mention it).
    assert "set -e" not in code_only, (
        "set -e would abort the wrapper on the first ffmpeg failure"
    )
    # Captured ffmpeg exit status enables the logger line below to
    # attribute restarts; if a future edit reintroduces ``|| true`` it
    # would silently zero out ``$?`` and the logs would all read
    # ``status 0``.  Pin the capture explicitly.
    assert "ffmpeg_status=$?" in code_only, (
        "Wrapper must capture ffmpeg's real exit status for logging"
    )
    assert "|| true" not in code_only, (
        "|| true after ffmpeg would mask the real exit code in $?"
    )


def test_camera_stream_wrapper_logs_restarts_via_unified_log(monkeypatch):
    """macOS unified-log integration: when ffmpeg dies and the wrapper
    restarts it, a tagged ``logger`` line lands in ``log show`` so the
    user can diagnose a hung restart loop without digging into the
    StandardError file."""
    macos = _load_macos(monkeypatch)
    rendered = macos._CAMERA_STREAM_WRAPPER_TEMPLATE.format(port=8091)

    assert "logger -t cyberwave-camera-stream" in rendered, (
        "Restarts must be visible via 'log show ... cyberwave-camera-stream'"
    )
    # Two logger calls: one announcing the initial start, one inside the
    # loop body for each restart.  Both must use the same tag so the
    # ``log show`` filter pulls a complete trail.
    assert rendered.count("logger -t cyberwave-camera-stream") >= 2, (
        "Expected both a startup line and a per-restart line"
    )
    assert "ffmpeg exited" in rendered, (
        "Restart line should mention ffmpeg + its captured exit status"
    )


def test_wait_for_launchd_unload_polls_until_label_disappears(monkeypatch):
    """Returns True after launchd reports the label is no longer loaded."""
    macos = _load_macos(monkeypatch)
    _stub_real_user(macos, monkeypatch)

    print_responses = iter([0, 0, 0, 1])

    def fake_subprocess_run(cmd, *args, **kwargs):
        if "print" in cmd:
            return SimpleNamespace(
                returncode=next(print_responses, 1), stdout=b"", stderr=b""
            )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(macos.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)

    assert macos.wait_for_launchd_unload("com.example.svc", timeout=2.0) is True


def test_wait_for_launchd_unload_stays_silent_when_label_already_gone(monkeypatch):
    """Happy path: bootout was instant, so the first poll already sees the
    label as unloaded.  We must NOT print the "Waiting for launchd…" line
    in that case — it would confuse users on a fresh install where there
    is nothing to wait on at all."""
    macos = _load_macos(monkeypatch)
    _stub_real_user(macos, monkeypatch)

    monkeypatch.setattr(
        macos.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=64, stdout=b"", stderr=b""),
    )
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)

    fake_console = MagicMock()
    monkeypatch.setattr(macos, "_get_console", lambda: fake_console)

    assert macos.wait_for_launchd_unload("com.example.svc", timeout=2.0) is True
    waiting_calls = [
        call for call in fake_console.print.call_args_list
        if "Waiting for launchd" in str(call)
    ]
    assert waiting_calls == [], (
        "No need to print a waiting line when the bootout was instant"
    )


def test_wait_for_launchd_unload_announces_wait_once(monkeypatch):
    """When the label is still loaded after the bootouts, surface a
    one-shot status line so the CLI doesn't appear frozen during the
    up-to-10s poll.  The line must be printed exactly once, regardless
    of how many poll iterations are required."""
    macos = _load_macos(monkeypatch)
    _stub_real_user(macos, monkeypatch)

    # 5 ``True`` responses then ``False`` → 5 poll iterations before the
    # label disappears; the waiting line must still appear exactly once.
    print_responses = iter([0, 0, 0, 0, 0, 1])

    def fake_subprocess_run(cmd, *args, **kwargs):
        if "print" in cmd:
            return SimpleNamespace(
                returncode=next(print_responses, 1), stdout=b"", stderr=b""
            )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(macos.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)

    fake_console = MagicMock()
    monkeypatch.setattr(macos, "_get_console", lambda: fake_console)

    assert macos.wait_for_launchd_unload("com.example.svc", timeout=5.0) is True

    waiting_calls = [
        call for call in fake_console.print.call_args_list
        if "Waiting for launchd" in str(call)
    ]
    assert len(waiting_calls) == 1, (
        f"Expected exactly one waiting-line announcement, got {len(waiting_calls)}"
    )
    assert "com.example.svc" in str(waiting_calls[0]), (
        "Waiting line should mention which label is being waited on"
    )


def test_wait_for_launchd_unload_times_out_when_label_stays_loaded(monkeypatch):
    """Returns False if launchctl print keeps reporting the label as loaded."""
    macos = _load_macos(monkeypatch)
    _stub_real_user(macos, monkeypatch)

    monotonic_values = iter([0.0, 0.1, 0.2, 3.0])

    monkeypatch.setattr(
        macos.time, "monotonic", lambda: next(monotonic_values, 99.0)
    )
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)
    monkeypatch.setattr(
        macos.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=b"", stderr=b""),
    )

    assert macos.wait_for_launchd_unload("com.example.svc", timeout=1.0) is False


def test_bootstrap_launchd_service_retries_exit_5_then_succeeds(monkeypatch):
    """Transient exit-5 should trigger a retry and eventually succeed."""
    macos = _load_macos(monkeypatch)
    attempts: list[int] = []

    def fake_run(cmd, *args, **kwargs):
        attempts.append(len(attempts) + 1)
        if len(attempts) < 2:
            raise subprocess.CalledProcessError(
                returncode=5, cmd=cmd, output=b"", stderr=b"5: I/O error"
            )
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(macos, "_run", fake_run)
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)

    macos.bootstrap_launchd_service(
        "gui/501", Path("/tmp/com.example.svc.plist"), retries=2
    )

    assert len(attempts) == 2, "first attempt failed, second attempt succeeded"


def test_bootstrap_launchd_service_does_not_retry_other_exit_codes(monkeypatch):
    """Non-5 exit codes (e.g. permission denied) must fail fast."""
    macos = _load_macos(monkeypatch)
    attempts: list[int] = []

    def fake_run(cmd, *args, **kwargs):
        attempts.append(len(attempts) + 1)
        raise subprocess.CalledProcessError(
            returncode=119, cmd=cmd, output=b"", stderr=b"permission denied"
        )

    monkeypatch.setattr(macos, "_run", fake_run)
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        macos.bootstrap_launchd_service(
            "gui/501", Path("/tmp/com.example.svc.plist"), retries=3
        )

    assert exc_info.value.returncode == 119
    assert len(attempts) == 1, "permission errors must not be retried"


def test_bootstrap_launchd_service_raises_after_exhausting_retries(monkeypatch):
    """If exit 5 persists across every retry, raise on the final attempt."""
    macos = _load_macos(monkeypatch)
    attempts: list[int] = []

    def fake_run(cmd, *args, **kwargs):
        attempts.append(len(attempts) + 1)
        raise subprocess.CalledProcessError(
            returncode=5, cmd=cmd, output=b"", stderr=b"5: I/O error"
        )

    monkeypatch.setattr(macos, "_run", fake_run)
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        macos.bootstrap_launchd_service(
            "gui/501", Path("/tmp/com.example.svc.plist"), retries=2
        )

    assert exc_info.value.returncode == 5
    assert len(attempts) == 3, "initial attempt + 2 retries"


# ---- edge-core label migration ----------------------------------------------


def test_teardown_edge_core_removes_legacy_dash_named_plist(monkeypatch, tmp_path):
    """Pre-unification CLI installs created com.cyberwave.edge-core.plist;
    teardown must clean it up alongside the current dot-named plist so users
    upgrading from older CLIs don't end up with orphan LaunchAgents."""
    macos = _load_macos(monkeypatch)

    launch_agents = tmp_path / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True)

    current_plist = launch_agents / "com.cyberwave.edge.core.plist"
    legacy_plist = launch_agents / "com.cyberwave.edge-core.plist"
    wrapper = tmp_path / ".cyberwave" / "edge_core.sh"
    wrapper.parent.mkdir(parents=True)

    for path in (current_plist, legacy_plist, wrapper):
        path.write_text("x", encoding="utf-8")

    monkeypatch.setattr(macos, "_user_home", lambda: tmp_path)
    monkeypatch.setattr(macos, "edge_core_plist_path", lambda: current_plist)
    monkeypatch.setattr(macos, "_edge_core_wrapper_path", lambda: wrapper)

    booted_out: list[str] = []
    monkeypatch.setattr(
        macos,
        "_bootout_launchd_service",
        lambda label: booted_out.append(label),
    )

    macos.teardown_edge_core_launchd_service()

    assert not current_plist.exists()
    assert not legacy_plist.exists()
    assert not wrapper.exists()
    assert "com.cyberwave.edge.core" in booted_out
    assert "com.cyberwave.edge-core" in booted_out


# ---- camera-stream auto-recovery on `edge restart` --------------------------


def _install_camera_stream_plist(tmp_path: Path, slot: int) -> Path:
    """Materialize a fake camera-stream plist for ``slot`` under tmp_path."""
    agents_dir = tmp_path / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    label = (
        "com.cyberwave.camera-stream"
        if slot == 0
        else f"com.cyberwave.camera-stream.{slot}"
    )
    plist = agents_dir / f"{label}.plist"
    plist.write_text("<plist/>", encoding="utf-8")
    return plist


def test_kickstart_unhealthy_camera_streams_only_kickstarts_silent_slots(
    monkeypatch, tmp_path
):
    """Slot 0 listening, slot 1 silent: only slot 1 gets `launchctl kickstart -k`.

    This pins the central UX promise: `edge restart` heals wedged camera
    bridges without dropping video on the healthy ones.
    """
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "_user_home", lambda: tmp_path)
    _stub_real_user(macos, monkeypatch, uid=501)

    _install_camera_stream_plist(tmp_path, 0)
    _install_camera_stream_plist(tmp_path, 1)

    listening_ports = {8091}

    def fake_is_port_listening(port):
        return port in listening_ports

    monkeypatch.setattr(macos, "_is_port_listening", fake_is_port_listening)
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)

    kickstarts: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        kickstarts.append(list(cmd))
        # Simulate ffmpeg coming up after the kickstart so the
        # post-wait health check turns green.
        listening_ports.add(8092)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(macos, "_run", fake_run)

    results = macos.kickstart_unhealthy_camera_streams()

    by_slot = {r["slot"]: r for r in results}
    assert by_slot[0]["was_listening"] is True
    assert by_slot[0]["kickstarted"] is False, "Healthy slot must be left alone"

    assert by_slot[1]["was_listening"] is False
    assert by_slot[1]["kickstarted"] is True
    assert by_slot[1]["healthy_after"] is True

    kickstart_targets = [
        cmd[cmd.index("kickstart") + 2]
        for cmd in kickstarts
        if "kickstart" in cmd
    ]
    assert kickstart_targets == ["gui/501/com.cyberwave.camera-stream.1"], (
        "Only the silent slot should be kickstarted, in the real user's GUI domain"
    )


def test_warn_on_camera_stream_config_drift_flags_orphan_twins(
    monkeypatch, tmp_path
):
    """A twin URL pointing at a port no installed slot serves must produce
    a yellow warning + a hint to ``--reconfigure-camera``.  Without this
    the operator's only feedback is silent driver retry loops in the logs."""
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "_user_home", lambda: tmp_path)

    _install_camera_stream_plist(tmp_path, 0)

    config_dir = tmp_path / ".cyberwave"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "camera_streams.json").write_text(
        '{"twin_to_stream_url": {'
        '"twin-served": "http://host.docker.internal:8091",'
        '"twin-orphaned": "http://host.docker.internal:8092"'
        "}}",
        encoding="utf-8",
    )

    fake_console = MagicMock()
    monkeypatch.setattr(macos, "_get_console", lambda: fake_console)

    orphans = macos.warn_on_camera_stream_config_drift()

    assert [o["twin_uuid"] for o in orphans] == ["twin-orphaned"]
    assert orphans[0]["port"] == 8092

    printed = " ".join(str(c) for c in fake_console.print.call_args_list)
    assert "twin-orphaned" in printed
    assert "8092" in printed
    assert "--reconfigure-camera" in printed
    assert "twin-served" not in printed, (
        "Healthy twin must not be mentioned"
    )


# ---- audio-stream auto-recovery on `edge restart` -----------------------------


def _install_audio_stream_plist(tmp_path: Path, slot: int) -> Path:
    """Materialize a fake audio-stream plist for ``slot`` under tmp_path."""
    agents_dir = tmp_path / "Library" / "LaunchAgents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    label = (
        "com.cyberwave.audio-stream"
        if slot == 0
        else f"com.cyberwave.audio-stream.{slot}"
    )
    plist = agents_dir / f"{label}.plist"
    plist.write_text("<plist/>", encoding="utf-8")
    return plist


def test_kickstart_unhealthy_audio_streams_only_kickstarts_silent_slots(
    monkeypatch, tmp_path
):
    """Slot 0 listening, slot 1 silent: only slot 1 gets `launchctl kickstart -k`."""
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "_user_home", lambda: tmp_path)
    _stub_real_user(macos, monkeypatch, uid=501)

    _install_audio_stream_plist(tmp_path, 0)
    _install_audio_stream_plist(tmp_path, 1)

    listening_ports = {8101}

    def fake_is_port_listening(port):
        return port in listening_ports

    monkeypatch.setattr(macos, "_is_port_listening", fake_is_port_listening)
    monkeypatch.setattr(macos.time, "sleep", lambda _s: None)

    kickstarts: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        kickstarts.append(list(cmd))
        listening_ports.add(8102)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(macos, "_run", fake_run)

    results = macos.kickstart_unhealthy_audio_streams()

    by_slot = {r["slot"]: r for r in results}
    assert by_slot[0]["was_listening"] is True
    assert by_slot[0]["kickstarted"] is False

    assert by_slot[1]["was_listening"] is False
    assert by_slot[1]["kickstarted"] is True
    assert by_slot[1]["healthy_after"] is True

    kickstart_targets = [
        cmd[cmd.index("kickstart") + 2]
        for cmd in kickstarts
        if "kickstart" in cmd
    ]
    assert kickstart_targets == ["gui/501/com.cyberwave.audio-stream.1"]


def test_warn_on_audio_stream_config_drift_flags_orphan_twins(
    monkeypatch, tmp_path
):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "is_macos", lambda: True)
    monkeypatch.setattr(macos, "_user_home", lambda: tmp_path)

    _install_audio_stream_plist(tmp_path, 0)

    config_dir = tmp_path / ".cyberwave"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "audio_streams.json").write_text(
        '{"twin_to_stream_url": {'
        '"twin-served": "http://host.docker.internal:8101",'
        '"twin-orphaned": "http://host.docker.internal:8102"'
        "}}",
        encoding="utf-8",
    )

    fake_console = MagicMock()
    monkeypatch.setattr(macos, "_get_console", lambda: fake_console)

    orphans = macos.warn_on_audio_stream_config_drift()

    assert [o["twin_uuid"] for o in orphans] == ["twin-orphaned"]
    assert orphans[0]["port"] == 8102

    printed = " ".join(str(c) for c in fake_console.print.call_args_list)
    assert "twin-orphaned" in printed
    assert "8102" in printed
    assert "--reconfigure-microphone" in printed
    assert "twin-served" not in printed
