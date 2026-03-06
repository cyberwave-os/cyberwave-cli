from pathlib import Path

import cyberwave_cli.config as config


def test_resolve_config_dir_uses_env_override(monkeypatch):
    monkeypatch.setenv("CYBERWAVE_EDGE_CONFIG_DIR", "/tmp/custom-cyberwave")
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")

    assert config._resolve_config_dir() == Path("/tmp/custom-cyberwave")


def test_resolve_config_dir_uses_invoking_user_home_on_macos(monkeypatch):
    monkeypatch.delenv("CYBERWAVE_EDGE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(config, "_resolve_sudo_user_home", lambda: Path("/Users/alice"))
    monkeypatch.setattr(config.Path, "home", lambda: Path("/var/root"))

    assert config._resolve_config_dir() == Path("/Users/alice/.cyberwave")


def test_resolve_config_dir_uses_current_home_on_macos_without_sudo(monkeypatch):
    monkeypatch.delenv("CYBERWAVE_EDGE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(config, "_resolve_sudo_user_home", lambda: None)
    monkeypatch.setattr(config.Path, "home", lambda: Path("/Users/bob"))

    assert config._resolve_config_dir() == Path("/Users/bob/.cyberwave")


def test_resolve_config_dir_uses_system_dir_on_linux_when_writable(tmp_path, monkeypatch):
    system_dir = tmp_path / "etc-cyberwave"
    user_dir = tmp_path / "user-cyberwave"
    system_dir.mkdir()

    monkeypatch.delenv("CYBERWAVE_EDGE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(config, "_SYSTEM_CONFIG_DIR", system_dir)
    monkeypatch.setattr(config, "_USER_CONFIG_DIR", user_dir)

    assert config._resolve_config_dir() == system_dir


def test_resolve_config_dir_creates_system_dir_on_linux_when_missing(tmp_path, monkeypatch):
    system_dir = tmp_path / "etc-cyberwave"
    user_dir = tmp_path / "user-cyberwave"

    monkeypatch.delenv("CYBERWAVE_EDGE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(config, "_SYSTEM_CONFIG_DIR", system_dir)
    monkeypatch.setattr(config, "_USER_CONFIG_DIR", user_dir)

    assert config._resolve_config_dir() == system_dir
    assert system_dir.exists()
    assert system_dir.is_dir()


def test_resolve_config_dir_falls_back_to_user_dir_when_system_dir_not_writable(
    tmp_path, monkeypatch
):
    system_dir = tmp_path / "etc-cyberwave"
    user_dir = tmp_path / "user-cyberwave"
    real_mkdir = config.Path.mkdir

    def _deny_system_dir_mkdir(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        if self == system_dir:
            raise PermissionError("permission denied")
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.delenv("CYBERWAVE_EDGE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(config, "_SYSTEM_CONFIG_DIR", system_dir)
    monkeypatch.setattr(config, "_USER_CONFIG_DIR", user_dir)
    monkeypatch.setattr(config.Path, "mkdir", _deny_system_dir_mkdir)

    assert config._resolve_config_dir() == user_dir


def test_resolve_config_dir_falls_back_when_system_dir_exists_but_is_not_writable(
    tmp_path, monkeypatch
):
    system_dir = tmp_path / "etc-cyberwave"
    user_dir = tmp_path / "user-cyberwave"
    system_dir.mkdir()

    monkeypatch.delenv("CYBERWAVE_EDGE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config.platform, "system", lambda: "Linux")
    monkeypatch.setattr(config, "_SYSTEM_CONFIG_DIR", system_dir)
    monkeypatch.setattr(config, "_USER_CONFIG_DIR", user_dir)
    monkeypatch.setattr(config.os, "access", lambda *_args, **_kwargs: False)

    assert config._resolve_config_dir() == user_dir
