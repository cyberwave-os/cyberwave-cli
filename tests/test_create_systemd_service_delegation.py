"""``create_systemd_service`` delegates the edge-core unit to the binary (plan B1)."""

import subprocess

import pytest
from _core_module_loader import load_core_module


def _setup(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    unit_path = tmp_path / "cyberwave-edge-core.service"
    monkeypatch.setattr(core.EDGE_CORE_SPEC, "unit_path", unit_path)
    monkeypatch.setattr(core, "_has_systemd", lambda: True)
    monkeypatch.setattr(
        core, "_resolve_service_binary", lambda spec: "/usr/bin/cyberwave-edge-core"
    )
    return core, unit_path


def test_edge_core_unit_is_written_by_the_binary(monkeypatch, tmp_path):
    core, unit_path = _setup(monkeypatch, tmp_path)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ensure-systemd-unit: wrote\n", stderr="")

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    assert core.create_systemd_service(core.EDGE_CORE_SPEC) is True

    assert calls == [
        [
            "/usr/bin/cyberwave-edge-core",
            "__ensure-systemd-unit",
            "--install",
            "--binary-path",
            "/usr/bin/cyberwave-edge-core",
            "--config-dir",
            str(core.CONFIG_DIR),
        ]
    ]
    # The binary wrote the unit, so the CLI template must not have.
    assert not unit_path.exists()


def test_edge_core_falls_back_to_cli_template_for_old_binaries(monkeypatch, tmp_path):
    core, unit_path = _setup(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        # What click prints for an unknown subcommand on a pre-B1 binary.
        return subprocess.CompletedProcess(
            cmd, 2, stdout="", stderr="Error: No such command '__ensure-systemd-unit'.\n"
        )

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    assert core.create_systemd_service(core.EDGE_CORE_SPEC) is True

    text = unit_path.read_text()
    assert "ExecStart=/usr/bin/cyberwave-edge-core\n" in text
    assert f"Environment=CYBERWAVE_EDGE_CONFIG_DIR={core.CONFIG_DIR}\n" in text
    assert "ExecStartPre" not in text


def _missing(cmd, timeout):
    raise FileNotFoundError(cmd[0])


def _hangs(cmd, timeout):
    raise subprocess.TimeoutExpired(cmd, timeout)


def _fails(cmd, timeout):
    return subprocess.CompletedProcess(
        cmd, 1, stdout="", stderr="ensure-systemd-unit: cannot write unit: boom\n"
    )


@pytest.mark.parametrize("binary", [_missing, _hangs, _fails], ids=["missing", "hangs", "fails"])
def test_edge_core_falls_back_to_cli_template(monkeypatch, tmp_path, binary):
    core, unit_path = _setup(monkeypatch, tmp_path)

    def fake_run(cmd, **kwargs):
        assert kwargs.get("timeout"), "the binary call must be bounded"
        return binary(cmd, kwargs["timeout"])

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    assert core.create_systemd_service(core.EDGE_CORE_SPEC) is True
    assert unit_path.exists()


def test_cloud_node_unit_is_not_delegated(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    unit_path = tmp_path / "cyberwave-cloud-node.service"
    monkeypatch.setattr(core.CLOUD_NODE_SPEC, "unit_path", unit_path)
    monkeypatch.setattr(core, "_has_systemd", lambda: True)

    def fake_run(cmd, **kwargs):
        raise AssertionError(f"unexpected subprocess call: {cmd}")

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    assert core.create_systemd_service(core.CLOUD_NODE_SPEC) is True
    assert unit_path.exists()
