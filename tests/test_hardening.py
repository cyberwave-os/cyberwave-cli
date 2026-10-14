"""Side effects and failure modes around the pin work: ownership, bad URLs, test isolation."""
from __future__ import annotations

import importlib
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from cyberwave_cli import device_utils, macos, utils

_login = importlib.import_module("cyberwave_cli.commands.login")


# ---- cameras.json ownership --------------------------------------------------------


def test_cameras_json_is_handed_back_to_the_sudo_user(monkeypatch, tmp_path):
    seen: list[tuple] = []
    monkeypatch.setattr(device_utils, "chown_to_sudo_user", lambda *paths: seen.append(paths))

    out = device_utils.write_cameras_json([], tmp_path, selected_index=None)

    assert seen == [(tmp_path / "cameras.json",)]
    assert out == tmp_path / "cameras.json"


# ---- a malformed CYBERWAVE_BASE_URL ---------------------------------------------------


@pytest.mark.parametrize(
    "url", ["http://h:8000X", "ftp://host/x", "http://", "host-without-scheme", "http://h:99999999"]
)
def test_login_with_a_malformed_base_url_exits_cleanly(monkeypatch, url):
    monkeypatch.setenv("CYBERWAVE_BASE_URL", url)
    monkeypatch.setattr(_login, "load_credentials", lambda: None)

    result = CliRunner().invoke(_login.login, ["--token", "cw_x"])

    assert result.exit_code == 1
    assert "CYBERWAVE_BASE_URL is not a valid URL" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "Traceback" not in result.output


def test_resolve_api_url_rejects_a_bad_environment_value_but_not_an_explicit_one(monkeypatch):
    monkeypatch.setenv("CYBERWAVE_BASE_URL", "http://h:8000X")
    with pytest.raises(Exception, match="not a valid URL"):
        utils.resolve_api_url()
    assert utils.resolve_api_url("https://api.example.test") == "https://api.example.test"
    monkeypatch.setenv("CYBERWAVE_BASE_URL", "http://192.168.1.2:8000")
    assert utils.resolve_api_url() == "http://192.168.1.2:8000"
    monkeypatch.setenv("CYBERWAVE_BASE_URL", "https://api.example.test/v1")
    assert utils.resolve_api_url() == "https://api.example.test/v1"


# ---- no sudo shell-out to reclaim a directory ---------------------------------------------


def test_reclaiming_ownership_never_shells_out_to_sudo(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(macos.subprocess, "run", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(macos, "_resolve_real_user", lambda: ("someone", os.getuid() + 1, 20))

    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    macos._fix_user_dir_ownership(tmp_path)
    assert calls == []  # not root: skipped

    chowned: list[tuple] = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda p, uid, gid: chowned.append((Path(p), uid, gid)))
    macos._fix_user_dir_ownership(tmp_path)
    assert calls == []
    assert chowned == [(tmp_path, os.getuid() + 1, 20)]


def test_a_directory_already_owned_by_the_user_is_left_alone(monkeypatch, tmp_path):
    chowned: list = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "chown", lambda *a: chowned.append(a))
    monkeypatch.setattr(macos, "_resolve_real_user", lambda: ("me", os.getuid(), 20))
    macos._fix_user_dir_ownership(tmp_path)
    assert chowned == []


# ---- the test run itself stays off this machine ----------------------------------------------


def test_the_run_is_sandboxed_away_from_the_real_home_and_sudo_identity():
    assert not any(k in os.environ for k in ("SUDO_USER", "SUDO_UID", "SUDO_GID"))
    assert os.environ["CYBERWAVE_EDGE_CONFIG_DIR"].startswith(os.environ["HOME"])
    assert "cw-cli-tests-" in os.environ["HOME"]


@pytest.mark.parametrize(
    "command", ["sudo", "systemctl", "docker", "git", "udevadm", "apt-get", "launchctl"]
)
def test_host_commands_are_refused_unless_a_test_asks_for_them(command):
    for runner in (subprocess.run, subprocess.check_output, subprocess.Popen):
        with pytest.raises(RuntimeError, match="host command"):
            runner([command, "--version"])


def test_other_commands_still_run():
    assert subprocess.run(["true"], check=True).returncode == 0


@pytest.mark.usefixtures("host_commands_missing")
def test_host_commands_can_be_made_to_look_absent():
    assert subprocess.run(["sudo", "true"]).returncode == 127
    with pytest.raises(FileNotFoundError):
        subprocess.check_output(["docker", "ps"])


def test_core_modules_do_not_read_this_hosts_devices():
    from tests._core_module_loader import load_core_module

    mp = pytest.MonkeyPatch()
    try:
        core = load_core_module(mp)
        for name in ("_V4L_BY_ID_DIR", "_V4L_BY_PATH_DIR", "_ALSA_BY_ID_DIR", "_ALSA_BY_PATH_DIR"):
            assert not os.path.exists(getattr(core, name))
        assert core._list_v4l_by_id() == [] and core._list_alsa_by_id() == []
        assert core._discover_usb_audio_cards() == []
        assert core._udev_properties("/dev/video0") == {}
    finally:
        mp.undo()
