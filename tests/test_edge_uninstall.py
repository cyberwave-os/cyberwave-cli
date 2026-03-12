import sys
import importlib
from types import ModuleType
from types import SimpleNamespace

edge_module = importlib.import_module("cyberwave_cli.commands.edge")


def test_stop_edge_driver_containers_returns_empty_when_docker_missing(monkeypatch):
    monkeypatch.setattr(edge_module.shutil, "which", lambda _: None)

    stopped = edge_module._stop_edge_driver_containers(lambda *_args, **_kwargs: None)

    assert stopped == []


def test_stop_edge_driver_containers_stops_matching_containers(monkeypatch):
    class _Result:
        stdout = "cyberwave-driver-123\ncyberwave-driver-456\n"

    issued_commands: list[list[str]] = []

    def _fake_run(command, **_kwargs):
        assert command[:3] == ["docker", "ps", "--format"]
        return _Result()

    def _fake_runner(command, check=False):
        issued_commands.append(command)
        assert check is False

    monkeypatch.setattr(edge_module.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(edge_module.subprocess, "run", _fake_run)

    stopped = edge_module._stop_edge_driver_containers(_fake_runner)

    assert stopped == ["cyberwave-driver-123", "cyberwave-driver-456"]
    assert issued_commands == [["docker", "stop", "cyberwave-driver-123", "cyberwave-driver-456"]]


def test_uninstall_edge_stops_driver_containers(monkeypatch, tmp_path):
    config_dir = tmp_path / "cyberwave-config"
    config_dir.mkdir()
    (config_dir / "credentials.json").write_text("{}\n", encoding="utf-8")

    unit_path = tmp_path / "cyberwave-edge-core.service"
    unit_path.write_text("[Unit]\nDescription=Test\n", encoding="utf-8")

    run_calls: list[list[str]] = []
    stop_calls: list[bool] = []

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.CONFIG_DIR = config_dir

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.PACKAGE_NAME = "cyberwave-edge-core"
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core.SYSTEMD_UNIT_PATH = unit_path
    fake_core._load_or_generate_edge_fingerprint = lambda: "fp-123"

    def _fake_run(command, *, check=True, **_kwargs):
        run_calls.append(command)
        return None

    fake_core._run = _fake_run

    fake_credentials = ModuleType("cyberwave_cli.credentials")
    fake_credentials.load_credentials = lambda: SimpleNamespace(
        token="token-123",
        workspace_uuid="workspace-123",
        cyberwave_base_url="https://api.example.com",
    )

    backend_cleanup_calls: list[dict] = []

    def _fake_backend_cleanup(**kwargs):
        backend_cleanup_calls.append(kwargs)
        return (1, 0)

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", fake_credentials)

    def _fake_stop_driver_containers(_runner):
        stop_calls.append(True)
        return ["cyberwave-driver-123"]

    monkeypatch.setattr(edge_module, "_stop_edge_driver_containers", _fake_stop_driver_containers)
    monkeypatch.setattr(
        edge_module, "_delete_registered_edges_for_fingerprint", _fake_backend_cleanup
    )

    edge_module.uninstall_edge.callback(yes=True)

    assert stop_calls == [True]
    assert ["systemctl", "stop", "cyberwave-edge-core.service"] in run_calls
    assert ["systemctl", "disable", "cyberwave-edge-core.service"] in run_calls
    assert not config_dir.exists()
    assert backend_cleanup_calls == [
        {
            "fingerprint": "fp-123",
            "token": "token-123",
            "base_url": "https://api.example.com",
            "workspace_uuid": "workspace-123",
        }
    ]


def test_delete_registered_edges_for_fingerprint_deletes_only_matching_workspace(monkeypatch):
    deleted_edge_ids: list[str] = []

    class _FakeEdges:
        def list(self):
            return [
                SimpleNamespace(uuid="edge-1", fingerprint="fp-abc", workspace_uuid="ws-1"),
                SimpleNamespace(uuid="edge-2", fingerprint="fp-abc", workspace_uuid="ws-2"),
                SimpleNamespace(uuid="edge-3", fingerprint="other-fp", workspace_uuid="ws-1"),
            ]

        def delete(self, edge_id):
            deleted_edge_ids.append(edge_id)

    class _FakeCyberwave:
        def __init__(self, *args, **kwargs):
            self.edges = _FakeEdges()

    fake_cyberwave_module = ModuleType("cyberwave")
    fake_cyberwave_module.Cyberwave = _FakeCyberwave

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.get_api_url = lambda: "https://api.example.com"

    monkeypatch.setitem(sys.modules, "cyberwave", fake_cyberwave_module)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)

    deleted_count, failed_count = edge_module._delete_registered_edges_for_fingerprint(
        fingerprint="fp-abc",
        token="token-123",
        base_url=None,
        workspace_uuid="ws-1",
    )

    assert (deleted_count, failed_count) == (1, 0)
    assert deleted_edge_ids == ["edge-1"]
