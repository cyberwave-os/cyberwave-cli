import json
import os
from pathlib import Path
from typer.testing import CliRunner
from unittest.mock import AsyncMock

from cyberwave_cli.core import app as core_app
from cyberwave_cli.plugins.edge.app import app as edge_app
import cyberwave_cli.plugins.edge.app as edge_module


core_app.add_typer(edge_app, name="edge")
runner = CliRunner()


def test_edge_run_invokes_subprocess(tmp_path, monkeypatch):
    cfg = tmp_path / "edge.json"
    cfg.write_text(json.dumps({"backend_url": "http://localhost:8000/api/v1"}))
    called = {}

    def fake_run(cmd, check=False):
        called["cmd"] = cmd
        called["check"] = check
        class R: ...
        return R()

    monkeypatch.setattr(edge_module.subprocess, "run", fake_run)
    result = runner.invoke(core_app, ["edge", "run", "--config", str(cfg)], prog_name="cyberwave")
    assert result.exit_code == 0
    assert called["cmd"][0:4] == ["python", "-m", "cyberwave_edge.main", "--config"]
    assert called["check"] is False


def test_edge_simulate_invokes_subprocess(monkeypatch):
    called = {}

    def fake_run(cmd, check=False):
        called["cmd"] = cmd
        called["check"] = check
        class R: ...
        return R()

    monkeypatch.setattr(edge_module.subprocess, "run", fake_run)
    result = runner.invoke(core_app, ["edge", "simulate", "--sensor", "s123", "--video", "vid.mp4", "--fps", "2.5"], prog_name="cyberwave")
    assert result.exit_code == 0
    assert called["cmd"][0:4] == ["python", "-m", "cyberwave_edge.camera_worker", "--sensor"]


def test_edge_init_writes_config(tmp_path, monkeypatch):
    cfg = tmp_path / "edge.json"
    dummy = type("D", (), {})()
    dummy.login = AsyncMock()
    dummy.register_device = AsyncMock(return_value={"id": 42})
    dummy.issue_device_token = AsyncMock(return_value="devtoken")
    dummy.aclose = AsyncMock()
    monkeypatch.setattr(edge_module, "Client", lambda *a, **k: dummy)

    result = runner.invoke(
        core_app,
        [
            "edge",
            "init",
            "--robot",
            "so_arm100",
            "--port",
            "/dev/ttyUSB0",
            "--backend",
            "http://localhost:8000/api/v1",
            "--project",
            "1",
            "--device-name",
            "edge-node",
            "--device-type",
            "robot/so-arm100",
            "--auto-register",
            "--use-device-token",
            "--config",
            str(cfg),
        ],
        prog_name="cyberwave",
    )
    assert result.exit_code == 0
    data = json.loads(cfg.read_text())
    assert data["backend_url"].endswith("/api/v1")
    assert data["device_id"] == 42
    assert data.get("access_token") == "devtoken"


def test_edge_status_success(tmp_path, monkeypatch):
    cfg = tmp_path / "edge.json"
    cfg.write_text(json.dumps({"backend_url": "http://localhost:8000/api/v1", "device_id": 99, "robot_type": "so_arm100"}))
    dummy = type("D", (), {})()
    dummy.login = AsyncMock()
    dummy.send_telemetry = AsyncMock(return_value={"ok": True})
    dummy.aclose = AsyncMock()
    monkeypatch.setattr(edge_module, "Client", lambda *a, **k: dummy)
    result = runner.invoke(core_app, ["edge", "status", "--config", str(cfg)], prog_name="cyberwave")
    assert result.exit_code == 0


def test_edge_status_failure(tmp_path, monkeypatch):
    cfg = tmp_path / "edge.json"
    cfg.write_text(json.dumps({"backend_url": "http://localhost:8000/api/v1", "device_id": 100, "robot_type": "so_arm100"}))
    dummy = type("D", (), {})()
    dummy.login = AsyncMock()
    async def _send(*a, **k):
        raise RuntimeError("fail")
    dummy.send_telemetry = AsyncMock(side_effect=_send)
    dummy.aclose = AsyncMock()
    monkeypatch.setattr(edge_module, "Client", lambda *a, **k: dummy)
    result = runner.invoke(core_app, ["edge", "status", "--config", str(cfg)], prog_name="cyberwave")
    assert result.exit_code != 0


