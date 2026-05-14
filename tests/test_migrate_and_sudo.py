"""Tests for _migrate_legacy_config_dir and the sudo escalation guard."""

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


def test_setup_service_sudo_guard_prevents_recursion(monkeypatch):
    core = load_core_module(monkeypatch)

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core, "_is_macos", lambda: False)
    monkeypatch.setattr(core.os, "geteuid", lambda: 1000)
    monkeypatch.setenv("_CYBERWAVE_SUDO_ESCALATED", "1")

    result = core.setup_service(core.EDGE_CORE_SPEC, skip_confirm=True)
    assert result is False


def test_setup_service_attempts_sudo_when_not_root(monkeypatch):
    core = load_core_module(monkeypatch)

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core, "_is_macos", lambda: False)
    monkeypatch.setattr(core.os, "geteuid", lambda: 1000)
    monkeypatch.delenv("_CYBERWAVE_SUDO_ESCALATED", raising=False)

    sudo_calls = []

    class FakeCompletedProcess:
        returncode = 0

    def fake_subprocess_run(cmd, **kwargs):
        sudo_calls.append(cmd)
        return FakeCompletedProcess()

    monkeypatch.setattr(core.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(core, "_migrate_legacy_config_dir", lambda: None)

    result = core.setup_service(core.EDGE_CORE_SPEC, skip_confirm=True)
    assert result is True
    assert len(sudo_calls) == 1
    assert sudo_calls[0][0] == "sudo"
    assert "_CYBERWAVE_SUDO_ESCALATED" in sudo_calls[0][1]


def test_setup_service_sudo_preserves_cyberwave_env_vars(monkeypatch):
    """Env vars like CYBERWAVE_BASE_URL must survive sudo escalation."""
    core = load_core_module(monkeypatch)

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core, "_is_macos", lambda: False)
    monkeypatch.setattr(core.os, "geteuid", lambda: 1000)
    monkeypatch.delenv("_CYBERWAVE_SUDO_ESCALATED", raising=False)

    monkeypatch.setenv("CYBERWAVE_BASE_URL", "https://api-dev.example.test")
    monkeypatch.setenv("CYBERWAVE_ENVIRONMENT", "dev")
    monkeypatch.setenv("CYBERWAVE_MQTT_HOST", "dev.mqtt.example.test")
    monkeypatch.setenv("CYBERWAVE_INTERNAL_DEB_READ_TOKEN", "tok_deb")

    sudo_calls: list[tuple[list[str], dict]] = []

    class FakeCompletedProcess:
        returncode = 0

    def fake_subprocess_run(cmd, **kwargs):
        sudo_calls.append((cmd, kwargs))
        return FakeCompletedProcess()

    monkeypatch.setattr(core.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(core, "_migrate_legacy_config_dir", lambda: None)

    result = core.setup_service(core.EDGE_CORE_SPEC, skip_confirm=True)
    assert result is True
    assert len(sudo_calls) == 1

    cmd, kwargs = sudo_calls[0]
    assert cmd[0] == "sudo"
    preserve_arg = cmd[1]
    assert preserve_arg.startswith("--preserve-env=")
    preserved_vars = preserve_arg.split("=", 1)[1].split(",")

    assert "CYBERWAVE_BASE_URL" in preserved_vars
    assert "CYBERWAVE_ENVIRONMENT" in preserved_vars
    assert "CYBERWAVE_MQTT_HOST" in preserved_vars
    assert "CYBERWAVE_INTERNAL_DEB_READ_TOKEN" in preserved_vars
    assert "CYBERWAVE_EDGE_CONFIG_DIR" in preserved_vars
    assert "_CYBERWAVE_SUDO_ESCALATED" in preserved_vars

    child_env = kwargs.get("env", {})
    assert child_env.get("CYBERWAVE_BASE_URL") == "https://api-dev.example.test"
    assert child_env.get("CYBERWAVE_ENVIRONMENT") == "dev"
    assert child_env.get("CYBERWAVE_MQTT_HOST") == "dev.mqtt.example.test"
