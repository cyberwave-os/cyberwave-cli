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
    for f in (plist, wrapper, log):
        f.write_text("test")

    monkeypatch.setattr(macos, "_camera_stream_plist_path", lambda: plist)
    monkeypatch.setattr(macos, "_camera_stream_wrapper_path", lambda: wrapper)
    monkeypatch.setattr(macos, "_camera_stream_log_path", lambda: log)
    monkeypatch.setattr(macos, "_bootout_launchd_service", lambda label: None)

    macos._teardown_camera_stream_server()

    assert not plist.exists()
    assert not wrapper.exists()
    assert not log.exists()


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
    monkeypatch.setattr(macos, "_camera_stream_wrapper_path", lambda: MagicMock())
    monkeypatch.setattr(macos, "_camera_stream_plist_path", lambda: MagicMock())
    monkeypatch.setattr(macos, "_camera_stream_log_path", lambda: MagicMock())
    monkeypatch.setattr(macos, "_chown_to_real_user", lambda *a, **kw: None)
    monkeypatch.setattr(macos, "_write_file_as_real_user", lambda *a, **kw: None)
    monkeypatch.setattr(macos, "_resolve_real_user", lambda: ("user", 501, 20))
    monkeypatch.setattr(macos, "_bootout_launchd_service", lambda *a, **kw: None)
    monkeypatch.setattr(macos, "_run", lambda *a, **kw: MagicMock())

    macos.setup_camera_stream_server(force=True)
    assert "teardown" in calls


# ---- init_console ------------------------------------------------------------


def test_init_console_injects_shared_instance(monkeypatch):
    macos = _load_macos(monkeypatch)
    mock_console = MagicMock()
    macos.init_console(mock_console)
    assert macos._get_console() is mock_console
