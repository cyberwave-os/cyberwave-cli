from tests._core_module_loader import load_core_module


def test_install_edge_core_defaults_to_stable_package(monkeypatch):
    core = load_core_module(monkeypatch)
    calls = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    monkeypatch.setattr(
        core,
        "_apt_get_install",
        lambda *, package_name, package_version: calls.append((package_name, package_version)) or True,
    )

    assert core.install_edge_core() is True
    assert calls == [("cyberwave-edge-core", None)]


def test_install_edge_core_uses_selected_channel_and_version(monkeypatch):
    core = load_core_module(monkeypatch)
    calls = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    monkeypatch.setattr(
        core,
        "_apt_get_install",
        lambda *, package_name, package_version: calls.append((package_name, package_version)) or True,
    )

    assert (
        core.install_edge_core(channel="staging", version="0.0.42.595") is True
    )
    assert calls == [("cyberwave-edge-core-staging", "0.0.42.595")]


def test_install_edge_core_rejects_non_stable_channel_without_apt(monkeypatch):
    core = load_core_module(monkeypatch)
    messages = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core.console, "print", lambda message="", *args, **kwargs: messages.append(str(message)))

    assert core.install_edge_core(channel="dev") is False
    assert any("Non-stable edge-core channels are only supported via apt-get" in message for message in messages)
