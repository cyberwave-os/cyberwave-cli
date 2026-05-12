"""Tests for the macOS pre-flight checks in ``setup_service``.

Pinned behavior: ``cyberwave edge install`` on macOS must abort early
with a copy-pasteable hint when Docker Desktop is not installed or its
daemon is not running. Without this, the install completed successfully
and the LaunchAgent immediately crash-looped trying to spawn driver
containers.
"""

from tests._core_module_loader import load_core_module


def _mock_macos(monkeypatch, core, *, is_macos: bool = True):
    import cyberwave_cli.macos as macos_mod

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_is_macos", lambda: is_macos)
    monkeypatch.setattr(core, "is_macos", lambda: is_macos)
    monkeypatch.setattr(macos_mod, "is_macos", lambda: is_macos)


def test_setup_service_aborts_on_macos_without_docker(monkeypatch):
    """The pre-flight bails out (False) before pip-installing edge-core,
    so we don't leave the user with a half-installed package + a
    LaunchAgent that can't run."""
    core = load_core_module(monkeypatch)

    _mock_macos(monkeypatch, core)
    # macOS install path aborts when running as root; pin a non-zero euid
    # so this test never reaches the sudo guard regardless of how CI runs.
    monkeypatch.setattr(core.os, "geteuid", lambda: 501)
    monkeypatch.setattr(core, "_ensure_credentials", lambda *, skip_confirm: True)

    install_called: list[bool] = []
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda *a, **kw: install_called.append(True) or True,
    )
    monkeypatch.setattr(core.shutil, "which", lambda name: None)

    messages: list[str] = []
    monkeypatch.setattr(
        core.console,
        "print",
        lambda message="", *a, **kw: messages.append(str(message)),
    )

    assert core.setup_edge_core(skip_confirm=True) is False
    assert install_called == [], (
        "Docker pre-flight must run before install_service_package so the "
        "user isn't left with a registered package + broken LaunchAgent."
    )
    blob = " ".join(messages)
    assert "Docker Desktop is required" in blob
    assert "docker.com" in blob, (
        "Hint must include the Docker Desktop install URL when Homebrew is absent."
    )


def test_macos_docker_check_uses_brew_hint_when_available(monkeypatch):
    """When Homebrew is on PATH, the hint should prefer the brew cask
    over the manual download URL — that's the path most macOS dev
    machines already use."""
    core = load_core_module(monkeypatch)

    def fake_which(name: str):
        return "/opt/homebrew/bin/brew" if name == "brew" else None

    monkeypatch.setattr(core.shutil, "which", fake_which)

    messages: list[str] = []
    monkeypatch.setattr(
        core.console,
        "print",
        lambda message="", *a, **kw: messages.append(str(message)),
    )

    assert core._check_docker_macos() is False
    blob = " ".join(messages)
    assert "brew install --cask docker" in blob
