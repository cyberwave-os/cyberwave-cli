from tests._core_module_loader import load_core_module


def test_setup_edge_core_non_linux_continues_without_service_setup(monkeypatch):
    core = load_core_module(monkeypatch)

    calls: list[tuple[str, bool | None]] = []
    messages: list[str] = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(
        core,
        "_ensure_credentials",
        lambda *, skip_confirm: calls.append(("credentials", skip_confirm)) or True,
    )
    monkeypatch.setattr(
        core,
        "install_edge_core",
        lambda *, channel, version: calls.append(("install", (channel, version))) or True,
    )
    monkeypatch.setattr(
        core,
        "configure_edge_environment",
        lambda *, skip_confirm: calls.append(("configure", skip_confirm)) or True,
    )
    monkeypatch.setattr(core, "_install_docker", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(core, "create_systemd_service", lambda: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(
        core,
        "enable_and_start_service",
        lambda: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(core.console, "print", lambda message="", *args, **kwargs: messages.append(str(message)))

    assert core.setup_edge_core(skip_confirm=True) is True
    assert calls == [("credentials", True), ("install", ("stable", None)), ("configure", True)]
    assert any(
        "Edge core service setup is only supported on Linux. "
        "You will need to start the core manually upon restart" in message
        for message in messages
    )


def test_setup_edge_core_non_linux_returns_false_when_config_fails(monkeypatch):
    core = load_core_module(monkeypatch)

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)
    monkeypatch.setattr(core, "install_edge_core", lambda *, channel, version: True)
    monkeypatch.setattr(core, "configure_edge_environment", lambda *, skip_confirm: False)

    assert core.setup_edge_core(skip_confirm=True) is False
