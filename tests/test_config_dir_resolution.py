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
