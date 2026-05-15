"""Tests for _migrate_legacy_config_dir and the upfront root privilege check."""

import json

from tests._core_module_loader import load_core_module


def test_migrate_copies_json_files_from_legacy_dir(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)

    legacy = tmp_path / "etc-cyberwave"
    target = tmp_path / "home-cyberwave"
    legacy.mkdir()
    target.mkdir()

    (legacy / "credentials.json").write_text(json.dumps({"token": "old"}))
    (legacy / "environment.json").write_text(json.dumps({"uuid": "env-1"}))
    (legacy / "extra.json").write_text(json.dumps({"key": "val"}))

    monkeypatch.setattr(core, "LEGACY_SYSTEM_CONFIG_DIR", legacy)
    monkeypatch.setattr(core, "CONFIG_DIR", target)

    core._migrate_legacy_config_dir()

    assert (target / "credentials.json").exists()
    assert json.loads((target / "credentials.json").read_text())["token"] == "old"
    assert (target / "environment.json").exists()
    assert (target / "extra.json").exists()


def test_migrate_does_not_overwrite_existing_files(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)

    legacy = tmp_path / "etc-cyberwave"
    target = tmp_path / "home-cyberwave"
    legacy.mkdir()
    target.mkdir()

    (legacy / "credentials.json").write_text(json.dumps({"token": "old"}))
    (target / "credentials.json").write_text(json.dumps({"token": "new"}))

    monkeypatch.setattr(core, "LEGACY_SYSTEM_CONFIG_DIR", legacy)
    monkeypatch.setattr(core, "CONFIG_DIR", target)

    core._migrate_legacy_config_dir()

    assert json.loads((target / "credentials.json").read_text())["token"] == "new"


def test_migrate_noop_when_legacy_dir_missing(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)

    legacy = tmp_path / "does-not-exist"
    target = tmp_path / "home-cyberwave"
    target.mkdir()

    monkeypatch.setattr(core, "LEGACY_SYSTEM_CONFIG_DIR", legacy)
    monkeypatch.setattr(core, "CONFIG_DIR", target)

    core._migrate_legacy_config_dir()

    assert list(target.iterdir()) == []


def test_migrate_noop_when_legacy_equals_target(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)

    same_dir = tmp_path / "cyberwave"
    same_dir.mkdir()
    (same_dir / "credentials.json").write_text(json.dumps({"token": "keep"}))

    monkeypatch.setattr(core, "LEGACY_SYSTEM_CONFIG_DIR", same_dir)
    monkeypatch.setattr(core, "CONFIG_DIR", same_dir)

    core._migrate_legacy_config_dir()

    # Should not crash and file should remain unchanged
    assert json.loads((same_dir / "credentials.json").read_text())["token"] == "keep"


def test_setup_service_exits_when_not_root_on_linux(monkeypatch):
    """setup_service must refuse to run on Linux when not root."""
    core = load_core_module(monkeypatch)

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core, "_is_macos", lambda: False)
    monkeypatch.setattr(core.os, "geteuid", lambda: 1000)

    result = core.setup_service(core.EDGE_CORE_SPEC, skip_confirm=True)
    assert result is False


def test_setup_service_proceeds_when_root_on_linux(monkeypatch):
    """setup_service must proceed when running as root on Linux."""
    core = load_core_module(monkeypatch)

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core, "_is_macos", lambda: False)
    monkeypatch.setattr(core.os, "geteuid", lambda: 0)
    monkeypatch.setattr(core, "_migrate_legacy_config_dir", lambda: None)
    monkeypatch.setattr(core, "_ensure_credentials", lambda skip_confirm=False: True)

    install_calls: list[tuple] = []
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda spec, channel="stable", version=None: install_calls.append(
            (spec, channel, version)
        )
        or True,
    )
    monkeypatch.setattr(core, "_install_docker", lambda: True)
    monkeypatch.setattr(core, "create_systemd_service", lambda spec: True)
    monkeypatch.setattr(core, "enable_and_start_service", lambda spec: True)
    monkeypatch.setattr(core, "_any_twin_has_camera_sensor", lambda: False)

    result = core.setup_service(core.EDGE_CORE_SPEC, skip_confirm=True)
    assert result is True
    assert len(install_calls) == 1
