"""Tests for cyberwave_cli.macos — USB/IP server setup on macOS.

Detection-logic tests for ``is_usbip_server_running`` and ``is_port_listening``
live in the SDK test suite (``tests/test_edge_platform.py``) since the
implementation now lives in ``cyberwave.edge.platform``.
"""

from pathlib import Path
from unittest.mock import MagicMock

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


def test_install_fails_without_cargo(monkeypatch):
    macos = _load_macos(monkeypatch)
    monkeypatch.setattr(macos, "_has_git", lambda: True)
    monkeypatch.setattr(macos, "_has_cargo", lambda: False)

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
    monkeypatch.setattr(macos, "_install_usbip_server", lambda: False)

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
        macos, "_install_usbip_server", lambda: calls.append("install") or True
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
    monkeypatch.setattr(macos, "_install_usbip_server", lambda: True)
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
