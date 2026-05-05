# tests/test_service_spec.py
import plistlib
import sys
from pathlib import Path

import httpx
import pytest
from _core_module_loader import load_core_module


def test_edge_core_spec_fields(monkeypatch):
    core = load_core_module(monkeypatch)
    assert core.EDGE_CORE_SPEC.package_name == "cyberwave-edge-core"
    assert core.EDGE_CORE_SPEC.unit_name == "cyberwave-edge-core.service"
    assert core.EDGE_CORE_SPEC.requires_docker is True


def test_edge_core_spec_aliases_match_module_constants(monkeypatch):
    core = load_core_module(monkeypatch)
    # Module-level constants must still match the spec (backward compat for edge.py imports)
    assert core.SYSTEMD_UNIT_NAME == core.EDGE_CORE_SPEC.unit_name
    assert core.SYSTEMD_UNIT_PATH == core.EDGE_CORE_SPEC.unit_path
    assert core.BINARY_PATH == core.EDGE_CORE_SPEC.binary_path
    assert core.BUILDKITE_KEYRING_PATH == core.EDGE_CORE_SPEC.keyring_path


def test_cloud_node_spec_fields(monkeypatch):
    core = load_core_module(monkeypatch)
    assert core.CLOUD_NODE_SPEC.package_name == "cyberwave-cloud-node"
    assert core.CLOUD_NODE_SPEC.unit_name == "cyberwave-cloud-node.service"
    assert core.CLOUD_NODE_SPEC.requires_docker is False
    assert "cyberwave-cloud-node" in core.CLOUD_NODE_SPEC.sources_list_path.name
    assert "cyberwave-cloud-node" in core.CLOUD_NODE_SPEC.gpg_key_url
    assert core.CLOUD_NODE_SPEC.sources_list_path != core.EDGE_CORE_SPEC.sources_list_path


def test_specs_have_distinct_process_match(monkeypatch):
    core = load_core_module(monkeypatch)
    assert core.CLOUD_NODE_SPEC.process_match != core.EDGE_CORE_SPEC.process_match


def test_resolve_deb_registry_urls_uses_internal_registry_for_dev(monkeypatch):
    core = load_core_module(monkeypatch)

    repo_url, key_url = core._resolve_deb_registry_urls(core.EDGE_CORE_SPEC, "dev")

    assert repo_url == "https://packages.buildkite.com/cyberwave/cyberwave-internal-deb/any/"
    assert key_url == "https://packages.buildkite.com/cyberwave/cyberwave-internal-deb/gpgkey"


def test_resolve_deb_registry_urls_uses_public_registry_for_stable(monkeypatch):
    core = load_core_module(monkeypatch)

    repo_url, key_url = core._resolve_deb_registry_urls(core.CLOUD_NODE_SPEC, "stable")

    assert repo_url == "https://packages.buildkite.com/cyberwave/cyberwave-cloud-node/any/"
    assert key_url == "https://packages.buildkite.com/cyberwave/cyberwave-cloud-node/gpgkey"


def test_stop_service_uses_spec_unit_name(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []

    unit_path = tmp_path / "cyberwave-cloud-node.service"
    unit_path.write_text("[Unit]\n")

    cloud_spec = core.CLOUD_NODE_SPEC
    monkeypatch.setattr(cloud_spec, "unit_path", unit_path)
    monkeypatch.setattr(core, "_has_systemd", lambda: True)
    monkeypatch.setattr(core, "_run", lambda cmd, **_kw: run_calls.append(cmd))

    core.stop_service(cloud_spec)

    assert any("cyberwave-cloud-node.service" in " ".join(cmd) for cmd in run_calls)


def test_is_service_active_uses_spec_unit_name(monkeypatch):
    core = load_core_module(monkeypatch)

    def fake_run(cmd, **_kw):
        class R:
            stdout = "active" if "cyberwave-cloud-node.service" in cmd else "inactive"
        return R()

    monkeypatch.setattr(core, "_has_systemd", lambda: True)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    assert core.is_service_active(core.CLOUD_NODE_SPEC) is True
    assert core.is_service_active(core.EDGE_CORE_SPEC) is False


def test_create_systemd_service_writes_unit_to_spec_path(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)

    unit_path = tmp_path / "cyberwave-cloud-node.service"
    cloud_spec = core.CLOUD_NODE_SPEC
    monkeypatch.setattr(cloud_spec, "unit_path", unit_path)
    monkeypatch.setattr(core, "_has_systemd", lambda: True)
    monkeypatch.setattr(cloud_spec, "binary_path", Path("/usr/bin/cyberwave-cloud-node"))

    result = core.create_systemd_service(cloud_spec)

    assert result is True
    assert unit_path.exists()
    assert "cyberwave-cloud-node" in unit_path.read_text()


def test_create_launchagent_service_writes_plist_with_config(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    home_dir = tmp_path / "home"
    binary_path = home_dir / "bin" / "cyberwave-cloud-node"
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_text("#!/bin/sh\n")

    monkeypatch.setattr(core.Path, "home", staticmethod(lambda: home_dir))
    monkeypatch.setattr(core.CLOUD_NODE_SPEC, "binary_path", binary_path)

    result = core.create_launchagent_service(
        core.CLOUD_NODE_SPEC,
        config_path="/tmp/cyberwave.yml",
    )

    assert result is True

    plist_path = home_dir / "Library" / "LaunchAgents" / "com.cyberwave.cloud-node.plist"
    assert plist_path.exists()
    plist_data = plistlib.loads(plist_path.read_bytes())
    assert plist_data["Label"] == "com.cyberwave.cloud-node"
    assert plist_data["ProgramArguments"] == [
        str(binary_path),
        "start",
        "--config",
        str(Path("/tmp/cyberwave.yml").resolve()),
    ]
    assert plist_data["EnvironmentVariables"]["PATH"] == (
        "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    )
    assert plist_data["RunAtLoad"] is True
    assert plist_data["KeepAlive"] is True


def test_load_launchagent_service_bootstraps_current_gui_user(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    bootout_calls: list[list[str]] = []
    bootstrap_calls: list[tuple[list[str], bool]] = []
    plist_path = tmp_path / "com.cyberwave.cloud-node.plist"
    plist_path.write_text("plist")

    monkeypatch.setattr(core, "_launchagent_plist_path", lambda spec: plist_path, raising=False)
    monkeypatch.setattr(core, "os", type("os", (), {"getuid": staticmethod(lambda: 501)})())

    def fake_subprocess_run(cmd, **_kwargs):
        bootout_calls.append(cmd)
        return type("R", (), {"returncode": 36})()

    monkeypatch.setattr(core.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        core,
        "_run",
        lambda cmd, **kwargs: bootstrap_calls.append((cmd, kwargs.get("check", True)))
        or type("R", (), {"returncode": 0})(),
    )

    result = core.load_launchagent_service(core.CLOUD_NODE_SPEC)

    assert result is True
    assert bootout_calls == [
        ["launchctl", "bootout", "gui/501/com.cyberwave.cloud-node"],
    ]
    assert bootstrap_calls == [
        (["launchctl", "bootstrap", "gui/501", str(plist_path)], True),
    ]


def test_write_service_override_quotes_config_path_and_reloads(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []

    unit_path = tmp_path / "cyberwave-cloud-node.service"
    override_dir = tmp_path / "cyberwave-cloud-node.service.d"
    config_path = tmp_path / "configs" / "with spaces" / "cyberwave.yml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("cyberwave-cloud-node:\n  profile_slug: default\n", encoding="utf-8")

    cloud_spec = core.CLOUD_NODE_SPEC
    monkeypatch.setattr(cloud_spec, "unit_path", unit_path)
    monkeypatch.setattr(cloud_spec, "binary_path", Path("/usr/bin/cyberwave-cloud-node"))
    monkeypatch.setattr(core, "_has_systemd", lambda: True)
    monkeypatch.setattr(core, "_run", lambda cmd, **_kw: run_calls.append(cmd))

    result = core.write_service_override(cloud_spec, config_path=str(config_path))

    override_file = override_dir / "override.conf"
    assert result is True
    assert override_file.exists()
    override_text = override_file.read_text(encoding="utf-8")
    assert "ExecStart=" in override_text
    assert str(config_path) in override_text
    assert f"'{config_path}'" in override_text
    assert run_calls == [["systemctl", "daemon-reload"]]


def test_clear_service_override_removes_file_and_reloads(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []

    unit_path = tmp_path / "cyberwave-cloud-node.service"
    override_file = tmp_path / "cyberwave-cloud-node.service.d" / "override.conf"
    override_file.parent.mkdir(parents=True)
    override_file.write_text("[Service]\nExecStart=\n", encoding="utf-8")

    cloud_spec = core.CLOUD_NODE_SPEC
    monkeypatch.setattr(cloud_spec, "unit_path", unit_path)
    monkeypatch.setattr(core, "_has_systemd", lambda: True)
    monkeypatch.setattr(core, "_run", lambda cmd, **_kw: run_calls.append(cmd))

    core.clear_service_override(cloud_spec)

    assert not override_file.exists()
    assert not override_file.parent.exists()
    assert run_calls == [["systemctl", "daemon-reload"]]


def _make_apt_recorder(calls):
    def _fake_apt(spec, *, package_name, package_version, channel="stable"):
        calls.append((package_name, package_version, channel))
        return True

    return _fake_apt


def _apt_which(name):
    return "/usr/bin/apt-get" if name == "apt-get" else None


def test_install_service_package_uses_cloud_node_spec(monkeypatch):
    core = load_core_module(monkeypatch)
    calls: list[tuple[str, str | None, str]] = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", _apt_which)
    monkeypatch.setattr(core, "_apt_get_install", _make_apt_recorder(calls))

    result = core.install_service_package(core.CLOUD_NODE_SPEC, channel="stable", version=None)
    assert result is True
    assert calls == [("cyberwave-cloud-node", None, "stable")]


def test_install_service_package_dev_channel_for_cloud_node(monkeypatch):
    core = load_core_module(monkeypatch)
    calls: list[tuple[str, str | None, str]] = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", _apt_which)
    monkeypatch.setattr(core, "_apt_get_install", _make_apt_recorder(calls))

    result = core.install_service_package(core.CLOUD_NODE_SPEC, channel="dev", version="1.2.3")
    assert result is True
    assert calls == [("cyberwave-cloud-node-dev", "1.2.3", "dev")]


def test_install_edge_core_alias_still_works(monkeypatch):
    """install_edge_core() must remain callable for backward compat."""
    core = load_core_module(monkeypatch)
    calls: list[tuple[str, str | None, str]] = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", _apt_which)
    monkeypatch.setattr(core, "_apt_get_install", _make_apt_recorder(calls))

    assert core.install_edge_core() is True
    assert calls == [("cyberwave-edge-core", None, "stable")]


def test_apt_get_install_uses_internal_registry_for_dev(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []

    keyring_path = tmp_path / "cyberwave-cloud-node.gpg"
    keyring_path.write_text("existing-key")
    sources_list_path = tmp_path / "cyberwave-cloud-node.list"
    binary_path = tmp_path / "cyberwave-cloud-node"

    def fake_run(cmd, **_kw):
        run_calls.append(cmd)
        if cmd[:3] == ["apt-get", "install", "-y"]:
            binary_path.write_text("#!/bin/sh\n")

    monkeypatch.setattr(
        core,
        "_resolve_deb_registry_paths",
        lambda spec, channel="stable": (keyring_path, sources_list_path),
    )
    monkeypatch.setattr(
        core,
        "_resolve_deb_registry_auth_conf_path",
        lambda spec, channel="stable": tmp_path / "cyberwave-cloud-node.auth.conf",
    )
    monkeypatch.setenv("CYBERWAVE_INTERNAL_DEB_READ_TOKEN", "test-read-token")
    monkeypatch.setattr(core.CLOUD_NODE_SPEC, "binary_path", binary_path)
    monkeypatch.setattr(core, "_run", fake_run)

    result = core._apt_get_install(
        core.CLOUD_NODE_SPEC,
        package_name="cyberwave-cloud-node-dev",
        package_version=None,
        channel="dev",
    )

    assert result is True
    assert "cyberwave-internal-deb" in sources_list_path.read_text(encoding="utf-8")



def test_apt_get_install_dev_requires_internal_token(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    messages: list[str] = []
    keyring_path = tmp_path / "cyberwave-cloud-node.gpg"
    keyring_path.write_text("existing-key")
    sources_list_path = tmp_path / "cyberwave-cloud-node.list"
    auth_conf_path = tmp_path / "cyberwave-cloud-node.auth.conf"

    monkeypatch.delenv("CYBERWAVE_INTERNAL_DEB_READ_TOKEN", raising=False)
    monkeypatch.setattr(
        core,
        "_resolve_deb_registry_paths",
        lambda spec, channel="stable": (keyring_path, sources_list_path),
    )
    monkeypatch.setattr(
        core,
        "_resolve_deb_registry_auth_conf_path",
        lambda spec, channel="stable": auth_conf_path,
    )
    monkeypatch.setattr(core.console, "print", lambda msg="", *a, **kw: messages.append(str(msg)))

    result = core._apt_get_install(
        core.CLOUD_NODE_SPEC,
        package_name="cyberwave-cloud-node-dev",
        package_version=None,
        channel="dev",
    )

    assert result is False
    assert any("CYBERWAVE_INTERNAL_DEB_READ_TOKEN" in message for message in messages)
    assert not auth_conf_path.exists()


def test_apt_get_install_writes_private_auth_for_dev(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []
    keyring_path = tmp_path / "cyberwave-cloud-node.gpg"
    keyring_path.write_text("existing-key")
    sources_list_path = tmp_path / "cyberwave-cloud-node.list"
    auth_conf_path = tmp_path / "cyberwave-cloud-node.auth.conf"
    binary_path = tmp_path / "cyberwave-cloud-node"

    def fake_run(cmd, **_kw):
        run_calls.append(cmd)
        if cmd[:3] == ["apt-get", "install", "-y"]:
            binary_path.write_text("#!/bin/sh\n")

    monkeypatch.setenv("CYBERWAVE_INTERNAL_DEB_READ_TOKEN", "test-read-token")
    monkeypatch.setattr(
        core,
        "_resolve_deb_registry_paths",
        lambda spec, channel="stable": (keyring_path, sources_list_path),
    )
    monkeypatch.setattr(
        core,
        "_resolve_deb_registry_auth_conf_path",
        lambda spec, channel="stable": auth_conf_path,
    )
    monkeypatch.setattr(core.CLOUD_NODE_SPEC, "binary_path", binary_path)
    monkeypatch.setattr(core, "_run", fake_run)

    result = core._apt_get_install(
        core.CLOUD_NODE_SPEC,
        package_name="cyberwave-cloud-node-dev",
        package_version=None,
        channel="dev",
    )

    assert result is True
    assert "password test-read-token" in auth_conf_path.read_text(encoding="utf-8")
    assert any(
        cmd[:2] == ["chmod", "600"] and str(auth_conf_path) in cmd
        for cmd in run_calls
    )


def test_apt_get_install_uses_saved_internal_token(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []
    keyring_path = tmp_path / "cyberwave-cloud-node.gpg"
    keyring_path.write_text("existing-key")
    sources_list_path = tmp_path / "cyberwave-cloud-node.list"
    auth_conf_path = tmp_path / "cyberwave-cloud-node.auth.conf"
    binary_path = tmp_path / "cyberwave-cloud-node"

    def fake_run(cmd, **_kw):
        run_calls.append(cmd)
        if cmd[:3] == ["apt-get", "install", "-y"]:
            binary_path.write_text("#!/bin/sh\n")

    saved_creds = type(
        "SavedCreds",
        (),
        {"token": "api-token", "internal_deb_read_token": "saved-deb-token"},
    )()
    monkeypatch.delenv("CYBERWAVE_INTERNAL_DEB_READ_TOKEN", raising=False)
    monkeypatch.setattr(core, "load_credentials", lambda: saved_creds)
    monkeypatch.setattr(
        core,
        "_resolve_deb_registry_paths",
        lambda spec, channel="stable": (keyring_path, sources_list_path),
    )
    monkeypatch.setattr(
        core,
        "_resolve_deb_registry_auth_conf_path",
        lambda spec, channel="stable": auth_conf_path,
    )
    monkeypatch.setattr(core.CLOUD_NODE_SPEC, "binary_path", binary_path)
    monkeypatch.setattr(core, "_run", fake_run)

    result = core._apt_get_install(
        core.CLOUD_NODE_SPEC,
        package_name="cyberwave-cloud-node-dev",
        package_version=None,
        channel="dev",
    )

    assert result is True
    assert "saved-deb-token" in auth_conf_path.read_text(encoding="utf-8")


def _raise_assertion(msg=""):
    def _inner(*args, **kwargs):
        raise AssertionError(msg or "should not be called")

    return _inner


def test_setup_service_cloud_node_non_linux(monkeypatch):
    core = load_core_module(monkeypatch)
    calls: list[str] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_is_macos", lambda: False, raising=False)
    monkeypatch.setattr(
        core,
        "_ensure_credentials",
        lambda *, skip_confirm: calls.append("creds") or True,
    )
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda spec, *, channel, version: calls.append(f"install:{spec.package_name}") or True,
    )
    monkeypatch.setattr(core, "_install_docker", _raise_assertion("should not be called"))
    monkeypatch.setattr(core, "create_systemd_service", _raise_assertion())
    monkeypatch.setattr(core, "enable_and_start_service", _raise_assertion())

    result = core.setup_service(
        core.CLOUD_NODE_SPEC, skip_confirm=True, channel="stable", version=None
    )

    assert result is True
    assert "creds" in calls
    assert "install:cyberwave-cloud-node" in calls


def test_setup_service_cloud_node_macos_creates_launchagent(monkeypatch):
    core = load_core_module(monkeypatch)
    calls: list[str] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_is_macos", lambda: True, raising=False)
    monkeypatch.setattr(core.os, "geteuid", lambda: 501)
    monkeypatch.setattr(
        core,
        "_ensure_credentials",
        lambda *, skip_confirm: calls.append("creds") or True,
    )
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda spec, *, channel, version: calls.append(f"install:{spec.package_name}") or True,
    )
    monkeypatch.setattr(core, "create_systemd_service", _raise_assertion())
    monkeypatch.setattr(core, "enable_and_start_service", _raise_assertion())
    monkeypatch.setattr(
        core,
        "create_launchagent_service",
        lambda spec, *, config_path=None: calls.append(f"plist:{config_path}") or True,
        raising=False,
    )
    monkeypatch.setattr(
        core,
        "load_launchagent_service",
        lambda spec: calls.append("launchctl") or True,
        raising=False,
    )

    result = core.setup_service(
        core.CLOUD_NODE_SPEC,
        skip_confirm=True,
        channel="stable",
        version=None,
        config_path="/tmp/cyberwave.yml",
    )

    assert result is True
    assert calls == [
        "creds",
        "install:cyberwave-cloud-node",
        "plist:/tmp/cyberwave.yml",
        "launchctl",
    ]


def test_setup_service_macos_rejects_sudo(monkeypatch):
    core = load_core_module(monkeypatch)
    messages: list[str] = []

    monkeypatch.setattr(core.console, "print", lambda msg="", *a, **kw: messages.append(str(msg)))
    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_is_macos", lambda: True, raising=False)
    monkeypatch.setattr(core.os, "geteuid", lambda: 0)

    result = core.setup_service(
        core.CLOUD_NODE_SPEC,
        skip_confirm=True,
        channel="stable",
        version=None,
    )

    assert result is False
    assert any("without sudo" in message.lower() for message in messages)


def test_setup_service_does_not_call_docker_when_requires_docker_false(monkeypatch):
    core = load_core_module(monkeypatch)
    docker_called = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core, "os", type("os", (), {"geteuid": staticmethod(lambda: 0)})())
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "install_service_package", lambda spec, *, channel, version: True)
    monkeypatch.setattr(core, "_install_docker", lambda: docker_called.append(True) or True)
    monkeypatch.setattr(core, "create_systemd_service", lambda spec=None: True)
    monkeypatch.setattr(core, "enable_and_start_service", lambda spec=None: True)

    core.setup_service(core.CLOUD_NODE_SPEC, skip_confirm=True, channel="stable", version=None)

    assert docker_called == [], "Docker should not be installed for cloud node"


def test_setup_service_calls_post_install_hook(monkeypatch):
    core = load_core_module(monkeypatch)
    hook_calls: list[bool] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_is_macos", lambda: False, raising=False)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "install_service_package", lambda spec, *, channel, version: True)

    core.setup_service(
        core.CLOUD_NODE_SPEC,
        skip_confirm=True,
        channel="stable",
        version=None,
        post_install_hook=lambda: hook_calls.append(True) or True,
    )

    assert hook_calls == [True]


def test_ensure_credentials_uses_env_api_key_without_prompt(monkeypatch):
    core = load_core_module(monkeypatch)
    saved_credentials: list[object] = []

    class FakeWorkspace:
        uuid = "ws-123"
        name = "Test Workspace"
        slug = "test-workspace"

    class FakeWorkspaces:
        @staticmethod
        def list():
            return [FakeWorkspace()]

    class FakeClient:
        workspaces = FakeWorkspaces()

    class FakeCredentials:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class _Status:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(core, "load_credentials", lambda: None)
    monkeypatch.setattr(
        core,
        "_get_sdk_client",
        lambda token, base_url=None: FakeClient(),
    )
    monkeypatch.setattr(
        core,
        "save_credentials",
        lambda credentials: saved_credentials.append(credentials),
    )
    monkeypatch.setattr(core, "Credentials", FakeCredentials)
    monkeypatch.setattr(
        core,
        "collect_runtime_env_overrides",
        lambda: {"CYBERWAVE_BASE_URL": "http://localhost:8000"},
    )
    monkeypatch.setattr(core.console, "status", lambda *args, **kwargs: _Status())
    monkeypatch.setattr(
        core.Prompt,
        "ask",
        lambda *args, **kwargs: (
            _ for _ in ()
        ).throw(AssertionError("Prompt should not be called")),
    )
    monkeypatch.setenv("CYBERWAVE_API_KEY", "token-123")
    monkeypatch.setenv("CYBERWAVE_WORKSPACE_SLUG", "test-workspace")

    result = core._ensure_credentials(skip_confirm=True)

    assert result is True
    assert len(saved_credentials) == 1
    assert saved_credentials[0].token == "token-123"
    assert saved_credentials[0].workspace_uuid == "ws-123"
    assert saved_credentials[0].workspace_name == "Test Workspace"


def test_buildkite_python_registry_index_url_uses_registry_slug(monkeypatch):
    core = load_core_module(monkeypatch)

    assert (
        core._buildkite_python_registry_index_url("cyberwave-internal-python")
        == "https://packages.buildkite.com/cyberwave/cyberwave-internal-python/pypi/simple"
    )


def test_fetch_available_versions_from_simple_index_parses_buildkite_links(monkeypatch):
    core = load_core_module(monkeypatch)
    pip_registry = sys.modules["cyberwave_cli.pip_registry"]
    html = """
    <html>
      <body>
        <a href="https://packages.buildkite.com/files/cyberwave-edge-core-0.1.2.dev7.tar.gz">a</a>
        <a href="https://packages.buildkite.com/files/cyberwave_edge_core-0.1.2.dev12-py3-none-any.whl">b</a>
        <a href="https://packages.buildkite.com/files/cyberwave-edge-core-0.1.2rc2.tar.gz">c</a>
      </body>
    </html>
    """

    class FakeResponse:
        text = html

        def raise_for_status(self):
            return None

    captured_requests: list[tuple[str, tuple[str, str] | None]] = []
    monkeypatch.setattr(
        pip_registry.httpx,
        "get",
        lambda url, *, auth=None, timeout=None: captured_requests.append((url, auth))
        or FakeResponse(),
    )

    versions = core._fetch_available_simple_index_versions(
        core._buildkite_python_registry_index_url("cyberwave-edge-core-python"),
        "cyberwave-edge-core",
    )

    assert captured_requests == [
        (
            "https://packages.buildkite.com/cyberwave/cyberwave-edge-core-python/pypi/simple/cyberwave-edge-core/",
            None,
        )
    ]
    assert versions == [
        core.Version("0.1.2.dev7"),
        core.Version("0.1.2.dev12"),
        core.Version("0.1.2rc2"),
    ]


def test_fetch_available_versions_uses_buildkite_auth_without_credentialed_url(monkeypatch):
    core = load_core_module(monkeypatch)
    pip_registry = sys.modules["cyberwave_cli.pip_registry"]

    class FakeResponse:
        text = """
        <a href="cyberwave-edge-core-0.1.2.dev7.tar.gz">a</a>
        """

        def raise_for_status(self):
            return None

    captured_requests: list[tuple[str, tuple[str, str] | None]] = []
    monkeypatch.setattr(
        pip_registry.httpx,
        "get",
        lambda url, *, auth=None, timeout=None: captured_requests.append((url, auth))
        or FakeResponse(),
    )

    versions = core._fetch_available_simple_index_versions(
        "https://packages.buildkite.com/cyberwave/cyberwave-internal-python/pypi/simple",
        "cyberwave-edge-core",
        buildkite_read_token="bkrt_secret-token",
    )

    assert versions == [core.Version("0.1.2.dev7")]
    assert captured_requests == [
        (
            "https://packages.buildkite.com/cyberwave/cyberwave-internal-python/pypi/simple/cyberwave-edge-core/",
            ("buildkite", "bkrt_secret-token"),
        )
    ]


def test_fetch_available_versions_redacts_buildkite_token_from_errors(monkeypatch):
    core = load_core_module(monkeypatch)
    pip_registry = sys.modules["cyberwave_cli.pip_registry"]

    def raise_connect_error(url, *, auth=None, timeout=None):
        raise httpx.ConnectError(
            "failed for https://buildkite:bkrt_secret-token@packages.buildkite.com/simple"
        )

    monkeypatch.setattr(pip_registry.httpx, "get", raise_connect_error)

    with pytest.raises(RuntimeError) as exc_info:
        core._fetch_available_simple_index_versions(
            "https://packages.buildkite.com/cyberwave/cyberwave-internal-python/pypi/simple",
            "cyberwave-edge-core",
            buildkite_read_token="bkrt_secret-token",
        )

    message = str(exc_info.value)
    assert "Failed to query available versions for cyberwave-edge-core" in message
    assert "bkrt_secret-token" not in message
    assert "buildkite:***@" in message


def test_select_pip_version_for_dev_channel_picks_highest_dev(monkeypatch):
    core = load_core_module(monkeypatch)
    versions = [
        core.Version("0.1.2.dev7"),
        core.Version("0.1.2.dev12"),
        core.Version("0.1.2rc2"),
    ]

    selected = core._select_pip_version_for_channel(
        versions,
        package_name="cyberwave-edge-core",
        channel="dev",
    )

    assert str(selected) == "0.1.2.dev12"


def test_select_pip_version_for_staging_channel_picks_highest_rc(monkeypatch):
    core = load_core_module(monkeypatch)
    versions = [
        core.Version("0.1.2.dev12"),
        core.Version("0.1.2rc2"),
        core.Version("0.1.2rc7"),
    ]

    selected = core._select_pip_version_for_channel(
        versions,
        package_name="cyberwave-edge-core",
        channel="staging",
    )

    assert str(selected) == "0.1.2rc7"


def test_validate_pip_channel_version_accepts_matching_explicit_version(monkeypatch):
    core = load_core_module(monkeypatch)

    resolved = core._validate_pip_channel_version(
        "cyberwave-edge-core",
        "0.3.1rc2",
        "staging",
    )

    assert str(resolved) == "0.3.1rc2"


def test_validate_pip_channel_version_rejects_channel_mismatch(monkeypatch):
    core = load_core_module(monkeypatch)

    try:
        core._validate_pip_channel_version(
            "cyberwave-cloud-node",
            "0.3.1rc2",
            "dev",
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected ValueError")

    assert "cyberwave-cloud-node" in message
    assert "does not match" in message


def test_pip_install_dev_lists_buildkite_versions_and_installs_latest_match(monkeypatch):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []
    messages: list[str] = []
    monkeypatch.setattr(
        core.console,
        "print",
        lambda msg="", *args, **kwargs: messages.append(str(msg)),
    )
    monkeypatch.setattr(
        core,
        "_fetch_available_simple_index_versions",
        lambda index_url, package_name, *, buildkite_read_token=None: [
            core.Version("0.1.2.dev7"),
            core.Version("0.1.2.dev12"),
            core.Version("0.1.2rc2"),
        ],
    )
    monkeypatch.setattr(core, "_run", lambda cmd, **_kw: run_calls.append(cmd))

    result = core._pip_install(core.EDGE_CORE_SPEC, channel="dev")

    assert result is False
    assert run_calls == []
    assert any("CYBERWAVE_INTERNAL_PYTHON_READ_TOKEN" in message for message in messages)


def test_pip_install_dev_uses_private_internal_python_registry(monkeypatch):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []
    messages: list[str] = []
    version_queries: list[tuple[str | None, str, str | None]] = []
    monkeypatch.setenv("CYBERWAVE_INTERNAL_PYTHON_READ_TOKEN", "test-python-token")
    monkeypatch.setattr(
        core.console,
        "print",
        lambda msg="", *args, **kwargs: messages.append(str(msg)),
    )

    def fake_fetch_versions(index_url, package_name, *, buildkite_read_token=None):
        version_queries.append((index_url, package_name, buildkite_read_token))
        return [
            core.Version("0.1.2.dev7"),
            core.Version("0.1.2.dev12"),
            core.Version("0.1.2rc2"),
        ]

    monkeypatch.setattr(core, "_fetch_available_simple_index_versions", fake_fetch_versions)
    monkeypatch.setattr(core, "_run", lambda cmd, **_kw: run_calls.append(cmd))

    result = core._pip_install(core.EDGE_CORE_SPEC, channel="dev")

    assert result is True
    assert version_queries == [
        (
            "https://packages.buildkite.com/cyberwave/cyberwave-internal-python/pypi/simple",
            "cyberwave-edge-core",
            "test-python-token",
        )
    ]
    assert run_calls == [
        [
            core.sys.executable,
            "-m",
            "pip",
            "install",
            "--pre",
            "--extra-index-url",
            "https://buildkite:test-python-token@packages.buildkite.com/cyberwave/cyberwave-internal-python/pypi/simple",
            "cyberwave-edge-core==0.1.2.dev12",
        ]
    ]
    assert any(
        "Resolved cyberwave-edge-core dev channel to 0.1.2.dev12" in message
        for message in messages
    )


def test_pip_install_stable_falls_back_when_version_query_fails(monkeypatch):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []
    messages: list[str] = []

    monkeypatch.setattr(
        core.console,
        "print",
        lambda msg="", *args, **kwargs: messages.append(str(msg)),
    )
    monkeypatch.setattr(core, "_run", lambda cmd, **_kw: run_calls.append(cmd))

    result = core._pip_install(core.EDGE_CORE_SPEC, channel="stable")

    assert result is True
    assert run_calls == [[core.sys.executable, "-m", "pip", "install", "cyberwave-edge-core"]]
    assert not any("Resolved cyberwave-edge-core stable channel" in message for message in messages)


def test_pip_install_prerelease_explicit_version_uses_buildkite_index(monkeypatch):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []
    messages: list[str] = []

    monkeypatch.setattr(
        core.console,
        "print",
        lambda msg="", *args, **kwargs: messages.append(str(msg)),
    )
    monkeypatch.setattr(core, "_run", lambda cmd, **_kw: run_calls.append(cmd))

    result = core._pip_install(
        core.CLOUD_NODE_SPEC,
        channel="staging",
        package_version="0.2.24rc7",
    )

    assert result is False
    assert run_calls == []
    assert any("CYBERWAVE_INTERNAL_PYTHON_READ_TOKEN" in message for message in messages)


def test_pip_install_prerelease_explicit_version_uses_private_index(monkeypatch):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []
    messages: list[str] = []

    monkeypatch.setenv("CYBERWAVE_INTERNAL_PYTHON_READ_TOKEN", "test-python-token")
    monkeypatch.setattr(
        core.console,
        "print",
        lambda msg="", *args, **kwargs: messages.append(str(msg)),
    )
    monkeypatch.setattr(core, "_run", lambda cmd, **_kw: run_calls.append(cmd))

    result = core._pip_install(
        core.CLOUD_NODE_SPEC,
        channel="staging",
        package_version="0.2.24rc7",
    )

    assert result is True
    assert run_calls == [
        [
            core.sys.executable,
            "-m",
            "pip",
            "install",
            "--pre",
            "--extra-index-url",
            "https://buildkite:test-python-token@packages.buildkite.com/cyberwave/cyberwave-internal-python/pypi/simple",
            "cyberwave-cloud-node==0.2.24rc7",
        ]
    ]
    assert any("Buildkite" in message for message in messages)


def test_pip_install_uses_saved_internal_python_token(monkeypatch):
    core = load_core_module(monkeypatch)
    run_calls: list[list[str]] = []
    messages: list[str] = []
    version_queries: list[tuple[str | None, str, str | None]] = []

    saved_creds = type(
        "SavedCreds",
        (),
        {"token": "api-token", "internal_python_read_token": "saved-python-token"},
    )()
    monkeypatch.delenv("CYBERWAVE_INTERNAL_PYTHON_READ_TOKEN", raising=False)
    monkeypatch.setattr(core, "load_credentials", lambda: saved_creds)
    monkeypatch.setattr(
        core.console,
        "print",
        lambda msg="", *args, **kwargs: messages.append(str(msg)),
    )
    def fake_fetch_versions(index_url, package_name, *, buildkite_read_token=None):
        version_queries.append((index_url, package_name, buildkite_read_token))
        return [
            core.Version("0.1.2.dev7"),
            core.Version("0.1.2.dev12"),
            core.Version("0.1.2rc2"),
        ]

    monkeypatch.setattr(core, "_fetch_available_simple_index_versions", fake_fetch_versions)
    monkeypatch.setattr(core, "_run", lambda cmd, **_kw: run_calls.append(cmd))

    result = core._pip_install(core.EDGE_CORE_SPEC, channel="dev")

    assert result is True
    assert version_queries == [
        (
            "https://packages.buildkite.com/cyberwave/cyberwave-internal-python/pypi/simple",
            "cyberwave-edge-core",
            "saved-python-token",
        )
    ]
    assert run_calls[0][6] == "https://buildkite:saved-python-token@packages.buildkite.com/cyberwave/cyberwave-internal-python/pypi/simple"


def test_install_service_package_uses_pip_for_nonstable_non_apt_channel(monkeypatch):
    core = load_core_module(monkeypatch)
    calls: list[tuple[str, str | None, str]] = []
    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(
        core,
        "_pip_install",
        lambda spec, *, package_version=None, channel="stable": (
            calls.append((spec.package_name, package_version, channel)) or True
        ),
    )

    result = core.install_service_package(core.CLOUD_NODE_SPEC, channel="dev", version="0.3.1.dev7")

    assert result is True
    assert calls == [("cyberwave-cloud-node", "0.3.1.dev7", "dev")]


def test_pip_install_mismatch_error_uses_spec_package_name(monkeypatch):
    """Channel mismatch errors must reference the actual package name."""
    core = load_core_module(monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(
        core.console, "print", lambda msg="", *a, **kw: messages.append(str(msg))
    )

    result = core._pip_install(
        core.CLOUD_NODE_SPEC,
        channel="dev",
        package_version="0.3.1rc2",
    )

    assert result is False
    combined = " ".join(messages)
    assert "cyberwave-cloud-node" in combined, "Error must mention the actual package name"
    assert "edge-core" not in combined, "Must not mention edge-core for cloud node"


def test_setup_service_non_linux_allows_nonstable_channels(monkeypatch):
    core = load_core_module(monkeypatch)
    install_calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_is_macos", lambda: False, raising=False)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda spec, *, channel, version: install_calls.append((channel, version)) or True,
    )

    result = core.setup_service(
        core.CLOUD_NODE_SPEC,
        skip_confirm=True,
        channel="dev",
        version="0.3.1.dev7",
    )

    assert result is True
    assert install_calls == [("dev", "0.3.1.dev7")]


def test_start_service_returns_false_when_no_systemd(monkeypatch):
    """start_service must return False (not None) when systemd is absent."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "_has_systemd", lambda: False)

    result = core.start_service(core.CLOUD_NODE_SPEC)

    assert result is False


def test_start_service_returns_false_on_systemctl_failure(monkeypatch, tmp_path):
    """start_service must return False when systemctl exits non-zero."""
    core = load_core_module(monkeypatch)

    unit_path = tmp_path / "cyberwave-cloud-node.service"
    unit_path.write_text("[Unit]\n")
    monkeypatch.setattr(core.CLOUD_NODE_SPEC, "unit_path", unit_path)
    monkeypatch.setattr(core, "_has_systemd", lambda: True)
    monkeypatch.setattr(
        core,
        "_run",
        lambda cmd, **_kw: type("R", (), {"returncode": 1})(),
    )

    result = core.start_service(core.CLOUD_NODE_SPEC)

    assert result is False


def test_start_service_returns_true_on_success(monkeypatch, tmp_path):
    """start_service must return True when systemctl succeeds."""
    core = load_core_module(monkeypatch)

    unit_path = tmp_path / "cyberwave-cloud-node.service"
    unit_path.write_text("[Unit]\n")
    monkeypatch.setattr(core.CLOUD_NODE_SPEC, "unit_path", unit_path)
    monkeypatch.setattr(core, "_has_systemd", lambda: True)
    monkeypatch.setattr(
        core,
        "_run",
        lambda cmd, **_kw: type("R", (), {"returncode": 0})(),
    )

    result = core.start_service(core.CLOUD_NODE_SPEC)

    assert result is True
