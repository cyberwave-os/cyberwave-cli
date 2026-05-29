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

edge_pkg_dir = commands_dir / "edge"
edge_spec = importlib.util.spec_from_file_location(
    "cyberwave_cli.commands.edge",
    edge_pkg_dir / "__init__.py",
    submodule_search_locations=[str(edge_pkg_dir)],
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
    fake_core.EDGE_CORE_SPEC = SimpleNamespace(package_name="cyberwave-edge-core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core.SYSTEMD_UNIT_PATH = unit_path
    fake_core._is_macos = lambda: False
    fake_core._load_or_generate_edge_fingerprint = lambda: "fp-123"
    fake_core._resolve_installed_edge_core_package_name = lambda: "cyberwave-edge-core"
    fake_core.require_root = lambda hint: None

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

    fake_macos = ModuleType("cyberwave_cli.macos")
    fake_macos.is_macos = lambda: False

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", fake_credentials)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.macos", fake_macos)

    def _fake_stop_driver_containers(_runner):
        stop_calls.append(True)
        return ["cyberwave-driver-123"]

    monkeypatch.setattr(edge_module, "_stop_edge_driver_containers", _fake_stop_driver_containers)
    monkeypatch.setattr(
        edge_module, "_delete_registered_edges_for_fingerprint", _fake_backend_cleanup
    )
    monkeypatch.setattr(edge_module, "_kill_lingering_edge_processes", lambda: None)
    monkeypatch.setattr(edge_module.os, "geteuid", lambda: 0)

    edge_module.uninstall_edge.callback(yes=True, channel=None)

    assert stop_calls == [True]
    assert ["systemctl", "stop", "cyberwave-edge-core.service"] in run_calls
    assert ["systemctl", "disable", "cyberwave-edge-core.service"] in run_calls
    assert ["rm", "-f", str(unit_path)] in run_calls
    assert ["systemctl", "daemon-reload"] in run_calls
    assert not config_dir.exists()
    assert backend_cleanup_calls == [
        {
            "fingerprint": "fp-123",
            "token": "token-123",
            "base_url": "https://api.example.com",
            "workspace_uuid": "workspace-123",
        }
    ]


def test_uninstall_edge_exits_when_not_root(monkeypatch, tmp_path):
    """Verify that uninstall exits with an error when not run as root."""
    config_dir = tmp_path / "cyberwave-config"
    config_dir.mkdir()
    (config_dir / "credentials.json").write_text("{}\n", encoding="utf-8")

    unit_path = tmp_path / "cyberwave-edge-core.service"
    unit_path.write_text("[Unit]\nDescription=Test\n", encoding="utf-8")

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.CONFIG_DIR = config_dir
    fake_config.clean_subprocess_env = lambda: {}

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.PACKAGE_NAME = "cyberwave-edge-core"
    fake_core.EDGE_CORE_SPEC = SimpleNamespace(package_name="cyberwave-edge-core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core.SYSTEMD_UNIT_PATH = unit_path
    fake_core._is_macos = lambda: False
    fake_core._load_or_generate_edge_fingerprint = lambda: "fp-123"
    fake_core._resolve_installed_edge_core_package_name = lambda: "cyberwave-edge-core"

    def _require_root_exits(hint):
        raise SystemExit(1)

    fake_core.require_root = _require_root_exits
    fake_core._run = lambda command, **_kw: None

    fake_credentials = ModuleType("cyberwave_cli.credentials")
    fake_credentials.load_credentials = lambda: None

    fake_macos = ModuleType("cyberwave_cli.macos")
    fake_macos.is_macos = lambda: False

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", fake_credentials)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.macos", fake_macos)
    monkeypatch.setattr(edge_module, "_stop_edge_driver_containers", lambda _runner: [])
    monkeypatch.setattr(
        edge_module,
        "_delete_registered_edges_for_fingerprint",
        lambda **_kwargs: (0, 0),
    )
    monkeypatch.setattr(edge_module, "_kill_lingering_edge_processes", lambda: None)
    monkeypatch.setattr(edge_module.os, "geteuid", lambda: 1000)

    try:
        edge_module.uninstall_edge.callback(yes=True, channel=None)
        raise AssertionError("Expected SystemExit(1)")
    except SystemExit as exc:
        assert exc.code == 1


def test_uninstall_edge_exits_before_confirmation_when_not_root(monkeypatch, tmp_path):
    """Root check must happen before any interactive prompts (CYB-1976).

    Running without sudo should fail immediately with a clear error, not ask
    for confirmation first and then prompt for credentials mid-uninstall.
    """
    config_dir = tmp_path / "cyberwave-config"
    config_dir.mkdir()

    confirmation_was_shown: list[bool] = []

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.CONFIG_DIR = config_dir

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.EDGE_CORE_SPEC = SimpleNamespace(package_name="cyberwave-edge-core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core.SYSTEMD_UNIT_PATH = tmp_path / "nonexistent.service"
    fake_core._is_macos = lambda: False
    fake_core._load_or_generate_edge_fingerprint = lambda: "fp-123"
    fake_core._resolve_installed_edge_core_package_name = lambda: "cyberwave-edge-core"

    def _require_root_exits(hint):
        raise SystemExit(1)

    fake_core.require_root = _require_root_exits
    fake_core._run = lambda command, **_kw: None

    fake_credentials = ModuleType("cyberwave_cli.credentials")
    fake_credentials.load_credentials = lambda: None

    fake_macos = ModuleType("cyberwave_cli.macos")
    fake_macos.is_macos = lambda: False

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", fake_credentials)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.macos", fake_macos)
    monkeypatch.setattr(edge_module, "_stop_edge_driver_containers", lambda _runner: [])
    monkeypatch.setattr(
        edge_module, "_delete_registered_edges_for_fingerprint", lambda **_kwargs: (0, 0)
    )
    monkeypatch.setattr(edge_module, "_kill_lingering_edge_processes", lambda: None)

    # Patch Confirm.ask to record if it was ever called
    original_confirm = edge_module.Confirm

    class _TrackingConfirm:
        @staticmethod
        def ask(*args, **kwargs):
            confirmation_was_shown.append(True)
            return False

    monkeypatch.setattr(edge_module, "Confirm", _TrackingConfirm)

    try:
        # Run without --yes so a confirmation would normally be shown
        edge_module.uninstall_edge.callback(yes=False, channel=None)
        raise AssertionError("Expected SystemExit(1)")
    except SystemExit as exc:
        assert exc.code == 1

    monkeypatch.setattr(edge_module, "Confirm", original_confirm)

    assert confirmation_was_shown == [], (
        "Confirmation prompt must not be shown before the root check — "
        "users should get an immediate error, not a false 'are you sure?' followed by a sudo prompt"
    )


def test_uninstall_edge_removes_detected_channel_package(monkeypatch, tmp_path):
    config_dir = tmp_path / "cyberwave-config"
    config_dir.mkdir()
    unit_path = tmp_path / "cyberwave-edge-core.service"

    run_calls: list[list[str]] = []

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.CONFIG_DIR = config_dir

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.PACKAGE_NAME = "cyberwave-edge-core"
    fake_core.EDGE_CORE_SPEC = SimpleNamespace(package_name="cyberwave-edge-core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core.SYSTEMD_UNIT_PATH = unit_path
    fake_core._is_macos = lambda: False
    fake_core._load_or_generate_edge_fingerprint = lambda: "fp-123"
    fake_core._resolve_installed_edge_core_package_name = lambda: "cyberwave-edge-core-dev"
    fake_core.require_root = lambda hint: None

    def _fake_run(command, *, check=True, **_kwargs):
        run_calls.append(command)
        return None

    fake_core._run = _fake_run

    fake_credentials = ModuleType("cyberwave_cli.credentials")
    fake_credentials.load_credentials = lambda: None

    fake_macos = ModuleType("cyberwave_cli.macos")
    fake_macos.is_macos = lambda: False

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", fake_credentials)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.macos", fake_macos)
    monkeypatch.setattr(edge_module, "_stop_edge_driver_containers", lambda _runner: [])
    monkeypatch.setattr(
        edge_module,
        "_delete_registered_edges_for_fingerprint",
        lambda **_kwargs: (0, 0),
    )
    monkeypatch.setattr(edge_module, "_kill_lingering_edge_processes", lambda: None)
    monkeypatch.setattr(edge_module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        edge_module.Confirm,
        "ask",
        lambda prompt, default=False: True,
    )

    edge_module.uninstall_edge.callback(yes=False, channel=None)

    assert ["apt-get", "remove", "-y", "cyberwave-edge-core-dev"] in run_calls


def test_uninstall_edge_macos_removes_launchagent_and_uninstalls_package(monkeypatch, tmp_path):
    config_dir = tmp_path / "cyberwave-config"
    config_dir.mkdir()
    (config_dir / "credentials.json").write_text("{}\n", encoding="utf-8")
    plist_path = tmp_path / "com.cyberwave.edge.core.plist"
    plist_path.write_text("plist", encoding="utf-8")
    calls: list[list[str]] = []

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.CONFIG_DIR = config_dir
    fake_config.clean_subprocess_env = lambda: {}

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.PACKAGE_NAME = "cyberwave-edge-core"
    fake_core.EDGE_CORE_SPEC = SimpleNamespace(package_name="cyberwave-edge-core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core.SYSTEMD_UNIT_PATH = tmp_path / "nonexistent.service"
    fake_core._is_macos = lambda: True
    fake_core._launchagent_target = lambda spec: ("gui/501", "gui/501/com.cyberwave.edge.core")
    fake_core._launchagent_plist_path = lambda spec: plist_path
    fake_core._resolve_installed_edge_core_package_name = lambda: "cyberwave-edge-core"
    fake_core._load_or_generate_edge_fingerprint = lambda: "fp-123"

    fake_credentials = ModuleType("cyberwave_cli.credentials")
    fake_credentials.load_credentials = lambda: None

    camera_teardown_calls: list[bool] = []

    fake_macos = ModuleType("cyberwave_cli.macos")
    fake_macos.is_macos = lambda: True
    fake_macos._teardown_camera_stream_server = lambda: camera_teardown_calls.append(True)
    fake_macos._teardown_audio_stream_server = lambda: None

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", fake_credentials)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.macos", fake_macos)
    monkeypatch.setattr(edge_module.os, "getuid", lambda: 501)
    monkeypatch.setattr(edge_module, "_stop_edge_driver_containers", lambda _runner: [])
    monkeypatch.setattr(
        edge_module, "_delete_registered_edges_for_fingerprint", lambda **_kwargs: (0, 0)
    )
    monkeypatch.setattr(edge_module, "_kill_lingering_edge_processes", lambda: None)

    def _fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(edge_module.subprocess, "run", _fake_run)

    edge_module.uninstall_edge.callback(yes=True, channel=None)

    assert not config_dir.exists()
    assert not plist_path.exists()
    assert camera_teardown_calls == [True]
    assert ["launchctl", "bootout", "gui/501/com.cyberwave.edge.core"] in calls
    assert [
        edge_module.sys.executable,
        "-m",
        "pip",
        "uninstall",
        "-y",
        "cyberwave-edge-core",
    ] in calls


def _make_linux_uninstall_fixtures(monkeypatch, tmp_path):
    """Set up common Linux uninstall mocks and return tracking containers."""
    config_dir = tmp_path / "cyberwave-config"
    config_dir.mkdir()
    unit_path = tmp_path / "cyberwave-edge-core.service"
    unit_path.write_text("[Unit]\nDescription=Test\n", encoding="utf-8")

    run_calls: list[list[str]] = []
    prune_container_calls: list[bool] = []
    prune_image_calls: list[bool] = []

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.CONFIG_DIR = config_dir

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.PACKAGE_NAME = "cyberwave-edge-core"
    fake_core.EDGE_CORE_SPEC = SimpleNamespace(package_name="cyberwave-edge-core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core.SYSTEMD_UNIT_PATH = unit_path
    fake_core._is_macos = lambda: False
    fake_core._load_or_generate_edge_fingerprint = lambda: "fp-123"
    fake_core._resolve_installed_edge_core_package_name = lambda: "cyberwave-edge-core"
    fake_core.require_root = lambda hint: None

    def _fake_run(command, *, check=True, **_kwargs):
        run_calls.append(command)
        return None

    fake_core._run = _fake_run

    fake_credentials = ModuleType("cyberwave_cli.credentials")
    fake_credentials.load_credentials = lambda: None

    fake_macos = ModuleType("cyberwave_cli.macos")
    fake_macos.is_macos = lambda: False

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", fake_credentials)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.macos", fake_macos)
    monkeypatch.setattr(edge_module, "_stop_edge_driver_containers", lambda _runner: [])
    monkeypatch.setattr(
        edge_module,
        "_delete_registered_edges_for_fingerprint",
        lambda **_kwargs: (0, 0),
    )
    monkeypatch.setattr(edge_module, "_kill_lingering_edge_processes", lambda: None)
    monkeypatch.setattr(edge_module.os, "geteuid", lambda: 0)

    def _fake_prune_containers():
        prune_container_calls.append(True)
        return 1

    def _fake_prune_images():
        prune_image_calls.append(True)
        return True

    monkeypatch.setattr(edge_module, "_prune_stopped_cyberwave_containers", _fake_prune_containers)
    monkeypatch.setattr(edge_module, "_prune_unused_docker_images", _fake_prune_images)

    return run_calls, prune_container_calls, prune_image_calls


def test_uninstall_edge_channel_dev_skips_docker_cleanup(monkeypatch, tmp_path):
    """When --channel=dev, Docker containers and images are preserved."""
    _, prune_container_calls, prune_image_calls = _make_linux_uninstall_fixtures(
        monkeypatch, tmp_path
    )

    edge_module.uninstall_edge.callback(yes=True, channel="dev")

    assert prune_container_calls == []
    assert prune_image_calls == []


def test_uninstall_edge_channel_staging_skips_docker_cleanup(monkeypatch, tmp_path):
    """When --channel=staging, Docker containers and images are preserved."""
    _, prune_container_calls, prune_image_calls = _make_linux_uninstall_fixtures(
        monkeypatch, tmp_path
    )

    edge_module.uninstall_edge.callback(yes=True, channel="staging")

    assert prune_container_calls == []
    assert prune_image_calls == []


def test_uninstall_edge_channel_stable_runs_docker_cleanup(monkeypatch, tmp_path):
    """When --channel=stable, Docker containers and images ARE cleaned up."""
    _, prune_container_calls, prune_image_calls = _make_linux_uninstall_fixtures(
        monkeypatch, tmp_path
    )

    edge_module.uninstall_edge.callback(yes=True, channel="stable")

    assert prune_container_calls == [True]
    assert prune_image_calls == [True]


def test_uninstall_edge_no_channel_runs_docker_cleanup(monkeypatch, tmp_path):
    """When --channel is not specified (None), Docker cleanup runs as before."""
    _, prune_container_calls, prune_image_calls = _make_linux_uninstall_fixtures(
        monkeypatch, tmp_path
    )

    edge_module.uninstall_edge.callback(yes=True, channel=None)

    assert prune_container_calls == [True]
    assert prune_image_calls == [True]


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
