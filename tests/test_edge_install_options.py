import importlib
import sys
import types

from click.testing import CliRunner

from tests._core_module_loader import load_core_module

_edge_module = importlib.import_module("cyberwave_cli.commands.edge")


def test_install_edge_core_defaults_to_stable_package(monkeypatch):
    core = load_core_module(monkeypatch)
    calls = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(
        core.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None
    )
    monkeypatch.setattr(
        core,
        "_apt_get_install",
        lambda spec, *, package_name, package_version: (
            calls.append((package_name, package_version)) or True
        ),
    )

    assert core.install_edge_core() is True
    assert calls == [("cyberwave-edge-core", None)]


def test_install_edge_core_uses_selected_channel_and_version(monkeypatch):
    core = load_core_module(monkeypatch)
    calls = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(
        core.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None
    )
    monkeypatch.setattr(
        core,
        "_apt_get_install",
        lambda spec, *, package_name, package_version: (
            calls.append((package_name, package_version)) or True
        ),
    )

    assert (
        core.install_edge_core(channel="staging", version="0.0.42.595") is True
    )
    assert calls == [("cyberwave-edge-core-staging", "0.0.42.595")]


def test_install_edge_core_uses_pip_for_nonstable_channel_without_apt(monkeypatch):
    core = load_core_module(monkeypatch)
    calls = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(
        core,
        "_pip_install",
        lambda spec, *, package_version=None, channel="stable": (
            calls.append((spec.package_name, package_version, channel)) or True
        ),
    )

    assert core.install_edge_core(channel="dev", version="0.3.1.dev5") is True
    assert calls == [("cyberwave-edge-core", "0.3.1.dev5", "dev")]


def test_edge_install_command_uses_channel_flag(monkeypatch):
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        _edge_module.install_edge,
        ["--channel", "dev", "--version", "0.3.1.dev5", "--yes"],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "skip_confirm": True,
            "channel": "dev",
            "version": "0.3.1.dev5",
            "force_reinstall": False,
        }
    ]


def test_edge_install_without_workers_flag_is_deprecated_noop(monkeypatch):
    """``--without-workers`` is accepted for backward compatibility but does not
    forward ``pull_worker_image`` to ``setup_edge_core``."""
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        _edge_module.install_edge,
        ["--without-workers", "--yes"],
    )

    assert result.exit_code == 0
    assert "deprecated" in result.output.lower()
    assert calls == [
        {
            "skip_confirm": True,
            "channel": "stable",
            "version": None,
            "force_reinstall": False,
        }
    ]


def test_edge_install_command_rejects_old_edge_core_channel_flag():
    runner = CliRunner()
    result = runner.invoke(_edge_module.install_edge, ["--edge-core-channel", "dev", "--yes"])

    assert result.exit_code != 0
    assert "No such option: --edge-core-channel" in result.output


def test_edge_install_command_rejects_old_edge_core_version_flag():
    runner = CliRunner()
    result = runner.invoke(_edge_module.install_edge, ["--edge-core-version", "0.3.1.dev5", "--yes"])

    assert result.exit_code != 0
    assert "No such option: --edge-core-version" in result.output
