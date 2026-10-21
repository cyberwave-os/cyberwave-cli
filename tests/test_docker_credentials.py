import io
import json

import pytest
from click.testing import CliRunner

from cyberwave_cli import docker_credentials as helper


def test_configure_preserves_other_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"credsStore": "desktop", "credHelpers": {"other": "gcloud"}}))
    result = CliRunner().invoke(helper.auth, ["configure-docker"])
    assert result.exit_code == 0
    config = json.loads(path.read_text())
    assert config["credsStore"] == "desktop"
    assert config["credHelpers"] == {"other": "gcloud", "registry.cyberwave.com": "cyberwave"}
    assert path.stat().st_mode & 0o777 == 0o600


def test_helper_uses_environment(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["docker-credential-cyberwave", "get"])
    monkeypatch.setattr("sys.stdin", io.StringIO("https://registry.cyberwave.com"))
    monkeypatch.setenv("CYBERWAVE_API_KEY", "cw_test")
    helper.main()
    assert json.loads(capsys.readouterr().out) == {"Username": "cyberwave", "Secret": "cw_test"}


def test_helper_never_sends_key_to_other_hosts(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["docker-credential-cyberwave", "get"])
    monkeypatch.setattr("sys.stdin", io.StringIO("registry.cyberwave.com.evil.test"))
    monkeypatch.setenv("CYBERWAVE_API_KEY", "cw_test")
    with pytest.raises(SystemExit):
        helper.main()
    assert "cw_test" not in capsys.readouterr().out


def test_helper_reports_not_found_without_key(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["docker-credential-cyberwave", "get"])
    monkeypatch.setattr("sys.stdin", io.StringIO("registry.cyberwave.com"))
    monkeypatch.delenv("CYBERWAVE_API_KEY", raising=False)
    monkeypatch.setattr(helper, "get_token", lambda: None)
    with pytest.raises(SystemExit):
        helper.main()
    assert capsys.readouterr().out.strip() == "credentials not found in native keychain"


def test_invalid_config_unchanged(tmp_path, monkeypatch):
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path))
    path = tmp_path / "config.json"
    path.write_text("invalid")
    assert CliRunner().invoke(helper.auth, ["configure-docker"]).exit_code != 0
    assert path.read_text() == "invalid"
