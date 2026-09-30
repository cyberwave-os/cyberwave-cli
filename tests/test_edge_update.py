"""``cyberwave edge update`` (edge-core auto-update plan B6)."""

import importlib
import subprocess
import sys
import types

import pytest
from click.testing import CliRunner

from tests._core_module_loader import load_core_module

_edge_module = importlib.import_module("cyberwave_cli.commands.edge")


class FakeApt:
    """Answers the dpkg/apt queries ``update_edge_core`` makes."""

    def __init__(self, *, installed=None, candidate=None, held=()):
        self.installed = dict(installed or {})  # package -> version
        self.candidate = candidate
        self.held = set(held)
        self.calls: list[list[str]] = []

    def run(self, cmd, **_kwargs):
        self.calls.append(list(cmd))
        if cmd[0] == "dpkg-query":
            version = self.installed.get(cmd[-1])
            if version is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout=f"installed {version}", stderr="")
        if cmd[:2] == ["apt-cache", "policy"]:
            candidate = self.candidate or "(none)"
            stdout = f"{cmd[-1]}:\n  Installed: x\n  Candidate: {candidate}\n"
            return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")
        if cmd[:2] == ["apt-mark", "showhold"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="\n".join(self.held), stderr="")
        if cmd[1:] == ["__ensure-systemd-unit"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[:2] == ["dpkg", "--compare-versions"]:
            newer = tuple(map(int, cmd[2].split("."))) > tuple(map(int, cmd[4].split(".")))
            return subprocess.CompletedProcess(cmd, 0 if newer else 1)
        raise AssertionError(f"unexpected command: {cmd}")


def _setup(monkeypatch, tmp_path, apt, *, root=True, unit=True, new_version="0.1.17"):
    core = load_core_module(monkeypatch)
    events: list[tuple] = []
    unit_path = tmp_path / "cyberwave-edge-core.service"
    if unit:
        unit_path.write_text("[Unit]\n")

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(core.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(core.os, "geteuid", lambda: 0 if root else 1000)
    monkeypatch.setattr(core.subprocess, "run", apt.run)
    monkeypatch.setattr(core, "_run", lambda cmd, **kw: events.append(("run", cmd)))
    monkeypatch.setattr(core, "_has_systemd", lambda: True)
    monkeypatch.setattr(core.EDGE_CORE_SPEC, "unit_path", unit_path)

    def fake_apt_get_install(spec, *, package_name, package_version, channel, extra_install_args):
        events.append(("install", package_name, package_version, channel, extra_install_args))
        apt.installed[package_name] = package_version or new_version
        return True

    monkeypatch.setattr(core, "_apt_get_install", fake_apt_get_install)
    monkeypatch.setattr(
        core,
        "create_systemd_service",
        lambda spec: (_ for _ in ()).throw(AssertionError("update must not re-template the unit")),
    )
    monkeypatch.setattr(core, "restart_service", lambda spec: events.append(("restart",)) or True)
    return core, events


def test_updates_installed_channel_and_restarts(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core-staging": "0.1.16"})
    core, events = _setup(monkeypatch, tmp_path, apt)

    assert core.update_edge_core() is True

    assert events == [
        ("install", "cyberwave-edge-core-staging", None, "staging", ()),
        ("restart",),
    ]
    # The unit is refreshed like the postinst does it: no flags, so the existing
    # ExecStart and config dir are kept.
    assert [str(core.EDGE_CORE_SPEC.binary_path), "__ensure-systemd-unit"] in apt.calls


def test_old_binary_without_the_subcommand_still_restarts(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core": "0.1.17"})
    core, events = _setup(monkeypatch, tmp_path, apt)
    real_run = apt.run

    def run(cmd, **kwargs):
        if cmd[1:] == ["__ensure-systemd-unit"]:
            raise FileNotFoundError(cmd[0])
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", run)

    assert core.update_edge_core(version="0.1.15") is True
    assert events[-1] == ("restart",)


def test_no_restart_when_already_up_to_date(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core": "0.1.17"})
    core, events = _setup(monkeypatch, tmp_path, apt, new_version="0.1.17")

    assert core.update_edge_core() is True

    assert events == [("install", "cyberwave-edge-core", None, "stable", ())]


def test_explicit_version_allows_downgrade(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core": "0.1.17"})
    core, events = _setup(monkeypatch, tmp_path, apt)

    assert core.update_edge_core(version="0.1.16") is True

    assert events[0] == (
        "install",
        "cyberwave-edge-core",
        "0.1.16",
        "stable",
        ("--allow-downgrades",),
    )
    assert events[-1] == ("restart",)


def test_held_package_is_refused_without_force(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core": "0.1.16"}, held={"cyberwave-edge-core"})
    core, events = _setup(monkeypatch, tmp_path, apt)

    assert core.update_edge_core() is False
    assert events == []


def test_held_package_is_updated_with_force(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core": "0.1.16"}, held={"cyberwave-edge-core"})
    core, events = _setup(monkeypatch, tmp_path, apt)

    assert core.update_edge_core(force=True) is True

    assert events[0] == (
        "install",
        "cyberwave-edge-core",
        None,
        "stable",
        ("--allow-change-held-packages",),
    )


def test_pr_channel_requires_version(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core-pr": "0.1.17~pr4416.g8632f31f9bce"})
    core, events = _setup(monkeypatch, tmp_path, apt)

    assert core.update_edge_core() is False
    assert events == []


def test_without_a_service_only_the_package_is_updated(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core": "0.1.16"})
    core, events = _setup(monkeypatch, tmp_path, apt, unit=False)

    assert core.update_edge_core() is True

    assert events == [("install", "cyberwave-edge-core", None, "stable", ())]


def test_requires_root(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core": "0.1.16"})
    core, events = _setup(monkeypatch, tmp_path, apt, root=False)

    with pytest.raises(SystemExit) as exc:
        core.update_edge_core()
    assert exc.value.code == 1
    assert events == []


def test_nothing_installed_via_apt(monkeypatch, tmp_path):
    core, events = _setup(monkeypatch, tmp_path, FakeApt())

    assert core.update_edge_core() is False
    assert events == []


def test_non_apt_host_is_refused(monkeypatch, tmp_path):
    core, events = _setup(monkeypatch, tmp_path, FakeApt())
    monkeypatch.setattr(core, "_is_linux", lambda: False)

    assert core.update_edge_core() is False
    assert events == []


def test_check_changes_nothing(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core-dev": "0.1.16"}, candidate="0.1.17")
    core, events = _setup(monkeypatch, tmp_path, apt, root=False)

    assert core.update_edge_core(check=True) is True

    assert events == []
    assert ["apt-cache", "policy", "cyberwave-edge-core-dev"] in apt.calls


def test_check_refreshes_the_index_as_root(monkeypatch, tmp_path):
    apt = FakeApt(installed={"cyberwave-edge-core": "0.1.16"}, candidate="0.1.16")
    core, events = _setup(monkeypatch, tmp_path, apt)

    assert core.update_edge_core(check=True) is True

    assert events == [("run", ["apt-get", "update", "-qq"])]


def test_candidate_version_parses_apt_cache_policy(monkeypatch, tmp_path):
    core, _ = _setup(monkeypatch, tmp_path, FakeApt(candidate="0.1.17"))
    assert core._apt_candidate_version("cyberwave-edge-core") == "0.1.17"

    core, _ = _setup(monkeypatch, tmp_path, FakeApt())
    assert core._apt_candidate_version("cyberwave-edge-core") is None


def test_update_command_forwards_options(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(update_edge_core=lambda **kwargs: calls.append(kwargs) or True),
    )

    result = CliRunner().invoke(_edge_module.update_edge, ["--version", "0.1.16", "--force"])

    assert result.exit_code == 0, result.output
    assert calls == [{"version": "0.1.16", "check": False, "force": True}]


def test_update_command_exits_nonzero_on_failure(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(update_edge_core=lambda **kwargs: False),
    )

    result = CliRunner().invoke(_edge_module.update_edge, ["--check"])

    assert result.exit_code == 1
