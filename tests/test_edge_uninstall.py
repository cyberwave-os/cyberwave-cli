import sys
import importlib
import importlib.util
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

rich_module = ModuleType("rich")
rich_console_module = ModuleType("rich.console")
rich_prompt_module = ModuleType("rich.prompt")
rich_table_module = ModuleType("rich.table")


class _Console:
    def print(self, *_args, **_kwargs):
        return None


class _Prompt:
    @staticmethod
    def ask(*_args, **_kwargs):
        return ""


class _Confirm:
    @staticmethod
    def ask(*_args, **_kwargs):
        return True


class _Table:
    def __init__(self, *args, **kwargs):
        pass

    def add_column(self, *args, **kwargs):
        return None

    def add_row(self, *args, **kwargs):
        return None


rich_console_module.Console = _Console
rich_prompt_module.Prompt = _Prompt
rich_prompt_module.Confirm = _Confirm
rich_table_module.Table = _Table
rich_module.console = rich_console_module
rich_module.prompt = rich_prompt_module
rich_module.table = rich_table_module

sys.modules.setdefault("rich", rich_module)
sys.modules.setdefault("rich.console", rich_console_module)
sys.modules.setdefault("rich.prompt", rich_prompt_module)
sys.modules.setdefault("rich.table", rich_table_module)

commands_dir = Path(__file__).resolve().parents[1] / "cyberwave_cli" / "commands"
commands_package = ModuleType("cyberwave_cli.commands")
commands_package.__path__ = [str(commands_dir)]
sys.modules.setdefault("cyberwave_cli.commands", commands_package)

edge_spec = importlib.util.spec_from_file_location(
    "cyberwave_cli.commands.edge",
    commands_dir / "edge.py",
)
assert edge_spec is not None and edge_spec.loader is not None
edge_module = importlib.util.module_from_spec(edge_spec)
sys.modules["cyberwave_cli.commands.edge"] = edge_module
edge_spec.loader.exec_module(edge_module)


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
    fake_core._resolve_installed_edge_core_package_name = (
        lambda: "cyberwave-edge-core"
    )

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


def test_uninstall_edge_removes_detected_channel_package(monkeypatch, tmp_path):
    config_dir = tmp_path / "cyberwave-config"
    config_dir.mkdir()
    unit_path = tmp_path / "cyberwave-edge-core.service"

    run_calls: list[list[str]] = []

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.CONFIG_DIR = config_dir

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.PACKAGE_NAME = "cyberwave-edge-core"
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core.SYSTEMD_UNIT_PATH = unit_path
    fake_core._load_or_generate_edge_fingerprint = lambda: "fp-123"
    fake_core._resolve_installed_edge_core_package_name = (
        lambda: "cyberwave-edge-core-dev"
    )

    def _fake_run(command, *, check=True, **_kwargs):
        run_calls.append(command)
        return None

    fake_core._run = _fake_run

    fake_credentials = ModuleType("cyberwave_cli.credentials")
    fake_credentials.load_credentials = lambda: None

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", fake_credentials)
    monkeypatch.setattr(edge_module, "_stop_edge_driver_containers", lambda _runner: [])
    monkeypatch.setattr(
        edge_module,
        "_delete_registered_edges_for_fingerprint",
        lambda **_kwargs: (0, 0),
    )
    monkeypatch.setattr(
        edge_module.Confirm,
        "ask",
        lambda prompt, default=False: True,
    )

    edge_module.uninstall_edge.callback(yes=False)

    assert ["apt-get", "remove", "-y", "cyberwave-edge-core-dev"] in run_calls


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
