import subprocess
from types import SimpleNamespace

import pytest

from tests._core_module_loader import load_core_module


def stopped(**overrides):
    fields = dict(LoadState="loaded", ActiveState="inactive", SubState="dead", MainPID="0", ControlPID="0", Job="")
    fields.update(overrides)
    return SimpleNamespace(returncode=0, stdout="\n".join(f"{k}={v}" for k, v in fields.items()))


@pytest.fixture
def core(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    unit = tmp_path / "edge.service"
    unit.touch()
    monkeypatch.setattr(core.EDGE_CORE_SPEC, "unit_path", unit)
    monkeypatch.setattr(core, "_has_systemd", lambda: True)
    monkeypatch.setattr(core, "require_root", lambda hint: None)
    return core


def test_restart_submits_bounded_job_without_waiting(core, monkeypatch):
    calls, printed = [], []
    monkeypatch.setattr(core, "_run", lambda cmd, **kw: calls.append((cmd, kw)))
    monkeypatch.setattr(core.console, "print", lambda s: printed.append(s))
    assert core.restart_service()
    assert calls == [(["systemctl", "restart", "--no-block", core.SYSTEMD_UNIT_NAME], {"timeout": 15})]
    assert any("Restart requested" in s for s in printed)
    assert not any("Service restarted" in s for s in printed)


def test_restart_rpc_timeout_returns_failure(core, monkeypatch):
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])
    monkeypatch.setattr(core, "_run", run)
    assert not core.restart_service()


def test_stop_replaces_pending_start_and_waits_for_process_and_job(core, monkeypatch):
    states = iter([stopped(ActiveState="deactivating", MainPID="42", Job="123"),
                   stopped(Job="123"), stopped()])
    calls = []
    def run(cmd, **kw):
        assert 0 < kw["timeout"] <= 15
        calls.append(cmd)
        return next(states) if cmd[1] == "show" else SimpleNamespace(returncode=0)
    monkeypatch.setattr(core, "_run", run)
    monkeypatch.setattr(core.time, "sleep", lambda _: None)
    assert core._stop_systemd_unit("edge.service")
    assert calls[0] == ["systemctl", "stop", "--no-block", "edge.service"]
    assert len(calls) == 4


@pytest.mark.parametrize("state", [stopped(ActiveState="deactivating", MainPID="42"),
                                   stopped(ControlPID="43"), stopped(Job="123"),
                                   SimpleNamespace(returncode=0, stdout="")])
def test_stop_has_total_wait_budget(core, monkeypatch, state):
    clock = [0.0]
    monkeypatch.setattr(core.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(core.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(core, "_run", lambda cmd, **kw: state if cmd[1] == "show" else SimpleNamespace(returncode=0))
    assert not core._stop_systemd_unit("edge.service", wait_seconds=3)
    assert clock[0] == 3


def test_already_removed_unit_can_be_uninstalled(core, monkeypatch):
    monkeypatch.setattr(core, "_run", lambda cmd, **kw: stopped(LoadState="not-found") if cmd[1] == "show" else SimpleNamespace(returncode=5))
    assert core._stop_systemd_unit("edge.service")


def test_failed_stop_cannot_be_treated_as_success(core, monkeypatch):
    monkeypatch.setattr(core, "_run", lambda cmd, **kw: stopped(ActiveState="active", MainPID="42") if cmd[1] == "show" else SimpleNamespace(returncode=1))
    assert not core._stop_systemd_unit("edge.service")


def test_install_bounds_all_systemd_commands(core, monkeypatch):
    calls = []
    monkeypatch.setattr(core, "_run", lambda cmd, **kw: calls.append((cmd, kw)))
    assert core.enable_and_start_service()
    assert all(kw["timeout"] == 15 for cmd, kw in calls)
    assert "--no-block" in calls[-1][0]
