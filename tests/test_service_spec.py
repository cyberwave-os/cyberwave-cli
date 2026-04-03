# tests/test_service_spec.py
from pathlib import Path

from tests._core_module_loader import load_core_module


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
    def _fake_apt(spec, *, package_name, package_version):
        calls.append((package_name, package_version))
        return True

    return _fake_apt


def _apt_which(name):
    return "/usr/bin/apt-get" if name == "apt-get" else None


def test_install_service_package_uses_cloud_node_spec(monkeypatch):
    core = load_core_module(monkeypatch)
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", _apt_which)
    monkeypatch.setattr(core, "_apt_get_install", _make_apt_recorder(calls))

    result = core.install_service_package(core.CLOUD_NODE_SPEC, channel="stable", version=None)
    assert result is True
    assert calls == [("cyberwave-cloud-node", None)]


def test_install_service_package_dev_channel_for_cloud_node(monkeypatch):
    core = load_core_module(monkeypatch)
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", _apt_which)
    monkeypatch.setattr(core, "_apt_get_install", _make_apt_recorder(calls))

    result = core.install_service_package(core.CLOUD_NODE_SPEC, channel="dev", version="1.2.3")
    assert result is True
    assert calls == [("cyberwave-cloud-node-dev", "1.2.3")]


def test_install_edge_core_alias_still_works(monkeypatch):
    """install_edge_core() must remain callable for backward compat."""
    core = load_core_module(monkeypatch)
    calls: list[tuple[str, str | None]] = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", _apt_which)
    monkeypatch.setattr(core, "_apt_get_install", _make_apt_recorder(calls))

    assert core.install_edge_core() is True
    assert calls == [("cyberwave-edge-core", None)]


def _raise_assertion(msg=""):
    def _inner(*args, **kwargs):
        raise AssertionError(msg or "should not be called")

    return _inner


def test_setup_service_cloud_node_non_linux(monkeypatch):
    core = load_core_module(monkeypatch)
    calls: list[str] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
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


def test_pip_install_error_uses_spec_package_name(monkeypatch):
    """Non-stable channel error must reference the actual package, not 'edge-core'."""
    core = load_core_module(monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(
        core.console, "print", lambda msg="", *a, **kw: messages.append(str(msg))
    )

    result = core._pip_install(core.CLOUD_NODE_SPEC, channel="dev")

    assert result is False
    combined = " ".join(messages)
    assert "cyberwave-cloud-node" in combined, "Error must mention the actual package name"
    assert "edge-core" not in combined, "Must not mention edge-core for cloud node"


def test_setup_service_non_linux_error_uses_spec_package_name(monkeypatch):
    """Non-Linux warning must reference the actual package, not 'edge-core'."""
    core = load_core_module(monkeypatch)
    messages: list[str] = []
    monkeypatch.setattr(
        core.console, "print", lambda msg="", *a, **kw: messages.append(str(msg))
    )
    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "install_service_package", lambda spec, *, channel, version: False)

    # Using a non-stable channel triggers the "not supported via pip" message
    core.setup_service(core.CLOUD_NODE_SPEC, skip_confirm=True, channel="dev", version=None)

    combined = " ".join(messages)
    assert "cyberwave-cloud-node" in combined
    assert "edge-core" not in combined


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
