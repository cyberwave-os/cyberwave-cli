from tests._core_module_loader import load_core_module


def _mock_is_macos(monkeypatch, core, value: bool):
    """Patch ``is_macos`` in both the core module and the macos sub-module."""
    import cyberwave_cli.macos as macos_mod

    monkeypatch.setattr(core, "_is_macos", lambda: value, raising=False)
    monkeypatch.setattr(core, "is_macos", lambda: value)
    monkeypatch.setattr(macos_mod, "is_macos", lambda: value)


def test_setup_edge_core_non_linux_continues_without_service_setup(monkeypatch):
    core = load_core_module(monkeypatch)

    calls: list[tuple[str, bool | None]] = []
    messages: list[str] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    _mock_is_macos(monkeypatch, core, False)
    monkeypatch.setattr(
        core,
        "_ensure_credentials",
        lambda *, skip_confirm: calls.append(("credentials", skip_confirm)) or True,
    )
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda spec, *, channel, version: calls.append(("install", (channel, version))) or True,
    )
    monkeypatch.setattr(
        core,
        "configure_edge_environment",
        lambda *, skip_confirm: calls.append(("configure", skip_confirm)) or True,
    )
    monkeypatch.setattr(core, "_install_docker", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(
        core,
        "create_systemd_service",
        lambda spec=None: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        core,
        "enable_and_start_service",
        lambda spec=None: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        core.console,
        "print",
        lambda message="", *args, **kwargs: messages.append(str(message)),
    )

    assert core.setup_edge_core(skip_confirm=True) is True
    assert calls == [("credentials", True), ("install", ("stable", None)), ("configure", True)]
    assert any(
        "service setup is only supported on Linux. "
        "You will need to start it manually upon restart" in message
        for message in messages
    )


def test_setup_edge_core_non_linux_returns_false_when_config_fails(monkeypatch):
    core = load_core_module(monkeypatch)

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    _mock_is_macos(monkeypatch, core, False)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "install_service_package", lambda spec, *, channel, version: True)
    monkeypatch.setattr(core, "configure_edge_environment", lambda *, skip_confirm: False)

    assert core.setup_edge_core(skip_confirm=True) is False


def test_setup_edge_core_macos_calls_usbip_setup(monkeypatch):
    core = load_core_module(monkeypatch)

    calls: list[str] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    _mock_is_macos(monkeypatch, core, True)
    monkeypatch.setattr(core, "_is_macos", lambda: True)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda spec, *, channel, version: True,
    )
    monkeypatch.setattr(
        core,
        "setup_usbip_server",
        lambda **kw: calls.append("usbip") or True,
    )
    monkeypatch.setattr(
        core,
        "setup_camera_stream_server",
        lambda **kw: calls.append("camera") or True,
    )
    monkeypatch.setattr(
        core,
        "configure_edge_environment",
        lambda *, skip_confirm: True,
    )
    monkeypatch.setattr(
        core,
        "create_launchagent_service",
        lambda spec, *, config_path=None: calls.append("plist") or True,
    )
    monkeypatch.setattr(
        core,
        "load_launchagent_service",
        lambda spec: calls.append("launchctl") or True,
    )

    assert core.setup_edge_core(skip_confirm=True) is True
    assert "usbip" in calls
    assert "camera" in calls


def test_setup_edge_core_macos_continues_when_usbip_fails(monkeypatch):
    core = load_core_module(monkeypatch)

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    _mock_is_macos(monkeypatch, core, True)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda spec, *, channel, version: True,
    )
    monkeypatch.setattr(core, "setup_usbip_server", lambda **kw: False)
    monkeypatch.setattr(core, "setup_camera_stream_server", lambda **kw: True)
    monkeypatch.setattr(
        core,
        "configure_edge_environment",
        lambda *, skip_confirm: True,
    )
    monkeypatch.setattr(core, "create_launchagent_service", lambda spec, *, config_path=None: True)
    monkeypatch.setattr(core, "load_launchagent_service", lambda spec: True)

    assert core.setup_edge_core(skip_confirm=True) is True


def test_setup_edge_core_force_reinstall_passes_force_to_helpers(monkeypatch):
    core = load_core_module(monkeypatch)

    usbip_kwargs: list[dict] = []
    camera_kwargs: list[dict] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    _mock_is_macos(monkeypatch, core, True)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda spec, *, channel, version: True,
    )
    monkeypatch.setattr(
        core,
        "setup_usbip_server",
        lambda **kw: usbip_kwargs.append(kw) or True,
    )
    monkeypatch.setattr(
        core,
        "setup_camera_stream_server",
        lambda **kw: camera_kwargs.append(kw) or True,
    )
    monkeypatch.setattr(
        core,
        "configure_edge_environment",
        lambda *, skip_confirm: True,
    )
    monkeypatch.setattr(core, "create_launchagent_service", lambda spec, *, config_path=None: True)
    monkeypatch.setattr(core, "load_launchagent_service", lambda spec: True)

    assert core.setup_edge_core(skip_confirm=True, force_reinstall=True) is True
    assert usbip_kwargs == [{"force": True}]
    assert camera_kwargs == [{"force": True}]


def test_setup_edge_core_default_does_not_force(monkeypatch):
    core = load_core_module(monkeypatch)

    usbip_kwargs: list[dict] = []
    camera_kwargs: list[dict] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    _mock_is_macos(monkeypatch, core, True)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda spec, *, channel, version: True,
    )
    monkeypatch.setattr(
        core,
        "setup_usbip_server",
        lambda **kw: usbip_kwargs.append(kw) or True,
    )
    monkeypatch.setattr(
        core,
        "setup_camera_stream_server",
        lambda **kw: camera_kwargs.append(kw) or True,
    )
    monkeypatch.setattr(
        core,
        "configure_edge_environment",
        lambda *, skip_confirm: True,
    )
    monkeypatch.setattr(core, "create_launchagent_service", lambda spec, *, config_path=None: True)
    monkeypatch.setattr(core, "load_launchagent_service", lambda spec: True)

    assert core.setup_edge_core(skip_confirm=True) is True
    assert usbip_kwargs == [{"force": False}]
    assert camera_kwargs == [{"force": False}]


def test_setup_edge_core_macos_rejects_sudo(monkeypatch):
    core = load_core_module(monkeypatch)
    messages: list[str] = []

    monkeypatch.setattr(core.console, "print", lambda message="", *a, **kw: messages.append(str(message)))
    monkeypatch.setattr(core, "_is_linux", lambda: False)
    _mock_is_macos(monkeypatch, core, True)
    monkeypatch.setattr(core, "_is_macos", lambda: True)
    monkeypatch.setattr(core.os, "geteuid", lambda: 0)

    result = core.setup_edge_core(skip_confirm=True)

    assert result is False
