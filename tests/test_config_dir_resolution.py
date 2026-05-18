from pathlib import Path

import cyberwave_cli.config as config


def test_resolve_config_dir_uses_env_override(monkeypatch):
    monkeypatch.setenv("CYBERWAVE_EDGE_CONFIG_DIR", "/tmp/custom-cyberwave")

    assert config._resolve_config_dir() == Path("/tmp/custom-cyberwave")


def test_resolve_config_dir_uses_invoking_user_home_under_sudo(monkeypatch):
    monkeypatch.delenv("CYBERWAVE_EDGE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config, "_resolve_sudo_user_home", lambda: Path("/home/alice"))
    monkeypatch.setattr(config.Path, "home", lambda: Path("/root"))

    assert config._resolve_config_dir() == Path("/home/alice/.cyberwave")


def test_resolve_config_dir_uses_current_home_without_sudo(monkeypatch):
    monkeypatch.delenv("CYBERWAVE_EDGE_CONFIG_DIR", raising=False)
    monkeypatch.setattr(config, "_resolve_sudo_user_home", lambda: None)
    monkeypatch.setattr(config.Path, "home", lambda: Path("/home/bob"))

    assert config._resolve_config_dir() == Path("/home/bob/.cyberwave")


def test_resolve_config_dir_env_override_wins_over_sudo_home(monkeypatch):
    monkeypatch.setenv("CYBERWAVE_EDGE_CONFIG_DIR", "/opt/custom")
    monkeypatch.setattr(config, "_resolve_sudo_user_home", lambda: Path("/home/alice"))

    assert config._resolve_config_dir() == Path("/opt/custom")
