import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

# --- stub rich (same pattern as test_edge_uninstall.py) ---
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
    def __init__(self, *a, **kw):
        pass

    def add_column(self, *a, **kw):
        return None

    def add_row(self, *a, **kw):
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

# --- load compute module ---
commands_dir = Path(__file__).resolve().parents[1] / "cyberwave_cli" / "commands"
commands_package = ModuleType("cyberwave_cli.commands")
commands_package.__path__ = [str(commands_dir)]
sys.modules.setdefault("cyberwave_cli.commands", commands_package)


def _load_compute_module(monkeypatch, fake_core=None, fake_config=None):
    fake_core = fake_core or ModuleType("cyberwave_cli.core")
    fake_config = fake_config or ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    sys.modules.pop("cyberwave_cli.commands.compute", None)
    spec = importlib.util.spec_from_file_location(
        "cyberwave_cli.commands.compute",
        commands_dir / "compute.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cyberwave_cli.commands.compute"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- Task 5: install + uninstall ----


def test_install_calls_setup_service(monkeypatch):
    calls = []

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core.setup_service = lambda spec, *, skip_confirm, channel, version: calls.append(
        (skip_confirm, channel, version)
    ) or True
    fake_core.write_service_override = lambda spec, config_path: True

    compute = _load_compute_module(monkeypatch, fake_core)
    compute.install_cloud_node.callback(yes=True, channel="stable", version=None, config_path=None)

    assert calls == [(True, "stable", None)]


def test_uninstall_skips_config_dir_removal(monkeypatch, tmp_path):
    """cloud uninstall must NOT remove ~/.cyberwave/"""
    config_dir = tmp_path / ".cyberwave"
    config_dir.mkdir()
    creds_file = config_dir / "credentials.json"
    creds_file.write_text("{}", encoding="utf-8")

    _unit_path = tmp_path / "cyberwave-cloud-node.service"
    _unit_path.write_text("[Unit]\n")

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = _unit_path
        sudo_command_hint = "sudo cyberwave compute install"

    run_calls = []
    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._run = lambda cmd, **_kw: run_calls.append(cmd)
    fake_core._resolve_installed_service_package_name = lambda spec: "cyberwave-cloud-node"
    fake_core.clear_service_override = lambda spec: None

    compute = _load_compute_module(monkeypatch, fake_core)
    compute.uninstall_cloud_node.callback(yes=True)

    assert creds_file.exists(), "credentials.json must NOT be removed by compute uninstall"
    assert any("stop" in cmd for cmd in run_calls)


def test_uninstall_aborts_on_no_confirmation(monkeypatch):
    calls = []

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._run = lambda *a, **kw: calls.append(a)
    fake_core._resolve_installed_service_package_name = lambda spec: "cyberwave-cloud-node"
    fake_core.clear_service_override = lambda spec: None

    compute = _load_compute_module(monkeypatch, fake_core)
    monkeypatch.setattr(compute.Confirm, "ask", lambda *a, **kw: False)

    compute.uninstall_cloud_node.callback(yes=False)

    assert calls == [], "No systemctl calls should be made after abort"


# ---- Task 6: start + stop + restart ----


def test_stop_without_systemd_sends_sigterm(monkeypatch):
    import signal

    killed: list[tuple[int, int]] = []

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")
        process_match = "cyberwave-cloud-node start"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core.stop_service = lambda spec: None

    compute = _load_compute_module(monkeypatch, fake_core)

    monkeypatch.setattr(
        compute.subprocess,
        "run",
        lambda cmd, **_kw: type("R", (), {"stdout": "12345\n", "returncode": 0})(),
    )
    monkeypatch.setattr(compute.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    compute.stop_cloud_node.callback()

    assert (12345, signal.SIGTERM) in killed


def test_start_non_systemd_spawns_binary(monkeypatch):
    spawned: list[list[str]] = []

    class FakeProc:
        pid = 99999

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")
        binary_path = Path("/usr/bin/cyberwave-cloud-node")

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core.start_service = lambda spec: None
    fake_core.enable_and_start_service = lambda spec: None
    fake_core.write_service_override = lambda spec, config_path: True

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    # Patch the helper directly — CLOUD_NODE_SPEC is a lazy import inside function bodies
    # and is not accessible as a module-level attribute of compute.py.
    monkeypatch.setattr(compute, "_find_cloud_node_binary", lambda: "/usr/bin/cyberwave-cloud-node")
    monkeypatch.setattr(
        compute.subprocess,
        "Popen",
        lambda cmd, **_kw: spawned.append(cmd) or FakeProc(),
    )

    compute.start_cloud_node.callback(config_path=None, foreground=False)

    assert len(spawned) == 1
    assert spawned[0][0] == "/usr/bin/cyberwave-cloud-node"


# ---- Task 7: status + logs ----


def test_status_shows_instance_identity(monkeypatch, tmp_path):
    import json

    identity_file = tmp_path / "instance_identity.json"
    identity_file.write_text(
        json.dumps({"uuid": "abc-123", "slug": "my-gpu-node"}), encoding="utf-8"
    )

    printed: list[str] = []

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()

    compute = _load_compute_module(monkeypatch, fake_core)
    monkeypatch.setattr(compute, "CLOUD_NODE_IDENTITY_FILE", identity_file)
    monkeypatch.setattr(
        compute.subprocess, "run", lambda *a, **kw: type("R", (), {"stdout": "inactive\n"})()
    )
    monkeypatch.setattr(compute.console, "print", lambda msg="", *a, **kw: printed.append(str(msg)))

    compute.status_cloud_node.callback()

    output = "\n".join(printed)
    assert "abc-123" in output
    assert "my-gpu-node" in output


def test_status_handles_missing_identity_file(monkeypatch, tmp_path):
    printed: list[str] = []

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()

    compute = _load_compute_module(monkeypatch, fake_core)
    monkeypatch.setattr(compute, "CLOUD_NODE_IDENTITY_FILE", tmp_path / "does_not_exist.json")
    monkeypatch.setattr(
        compute.subprocess, "run", lambda *a, **kw: type("R", (), {"stdout": "inactive\n"})()
    )
    monkeypatch.setattr(compute.console, "print", lambda msg="", *a, **kw: printed.append(str(msg)))

    compute.status_cloud_node.callback()  # must not raise

    assert any("not yet registered" in p for p in printed)


# ---- uninstall --yes removes package ----


def test_uninstall_yes_removes_package(monkeypatch, tmp_path):
    """--yes should answer yes to ALL prompts, including package removal."""
    _unit_path = tmp_path / "cyberwave-cloud-node.service"
    _unit_path.write_text("[Unit]\n")

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = _unit_path
        sudo_command_hint = "sudo cyberwave compute install"

    run_calls: list[list[str]] = []

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._run = lambda cmd, **_kw: run_calls.append(cmd)
    fake_core._resolve_installed_service_package_name = lambda spec: "cyberwave-cloud-node"
    fake_core.clear_service_override = lambda spec: None

    compute = _load_compute_module(monkeypatch, fake_core)
    compute.uninstall_cloud_node.callback(yes=True)

    apt_remove_calls = [cmd for cmd in run_calls if "apt-get" in cmd and "remove" in cmd]
    assert apt_remove_calls, "apt-get remove should be called when --yes is passed"
    assert "cyberwave-cloud-node" in apt_remove_calls[0]


def test_uninstall_no_confirmation_skips_package_removal(monkeypatch, tmp_path):
    """Declining both confirmation prompts should remove neither service nor package."""
    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = tmp_path / "nonexistent.service"
        sudo_command_hint = "sudo cyberwave compute install"

    run_calls: list = []
    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._run = lambda cmd, **_kw: run_calls.append(cmd)
    fake_core._resolve_installed_service_package_name = lambda spec: "cyberwave-cloud-node"
    fake_core.clear_service_override = lambda spec: None

    compute = _load_compute_module(monkeypatch, fake_core)
    # First confirm (remove service) → False; second (remove package) → False
    confirm_responses = iter([False, False])
    monkeypatch.setattr(compute.Confirm, "ask", lambda *a, **kw: next(confirm_responses))

    compute.uninstall_cloud_node.callback(yes=False)

    apt_calls = [cmd for cmd in run_calls if "apt-get" in cmd]
    assert apt_calls == [], "No apt-get calls when user declines both prompts"


# ---- restart fallback with options ----


def test_restart_non_systemd_passes_options_to_binary(monkeypatch):
    spawned: list[list[str]] = []

    class FakeProc:
        pid = 77777

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")
        binary_path = Path("/usr/bin/cyberwave-cloud-node")
        process_match = "cyberwave-cloud-node start"
        sudo_command_hint = "sudo cyberwave compute install"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core.restart_service = lambda spec: None
    fake_core.write_service_override = lambda spec, config_path: True

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(compute, "_find_cloud_node_binary", lambda: "/usr/bin/cyberwave-cloud-node")
    monkeypatch.setattr(
        compute.subprocess,
        "run",
        lambda cmd, **_kw: type("R", (), {"stdout": "", "returncode": 0})(),
    )
    monkeypatch.setattr(
        compute.subprocess,
        "Popen",
        lambda cmd, **_kw: spawned.append(cmd) or FakeProc(),
    )

    compute.restart_cloud_node.callback(config_path=None)

    assert len(spawned) == 1
    assert spawned[0][0] == "/usr/bin/cyberwave-cloud-node"


# ---- config_path parameter ----


def test_start_non_systemd_with_config_path_passes_flag(monkeypatch, tmp_path):
    """--config-path should append --config <path> to the binary args (non-systemd)."""
    spawned: list[list[str]] = []
    config_file = tmp_path / "cyberwave.yml"
    config_file.write_text("cyberwave-cloud-node:\n  profile_slug: default\n")

    class FakeProc:
        pid = 11111

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")
        binary_path = Path("/usr/bin/cyberwave-cloud-node")

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core.start_service = lambda spec: None
    fake_core.enable_and_start_service = lambda spec: None
    fake_core.write_service_override = lambda spec, config_path: True

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(compute, "_find_cloud_node_binary", lambda: "/usr/bin/cyberwave-cloud-node")
    monkeypatch.setattr(
        compute.subprocess,
        "Popen",
        lambda cmd, **_kw: spawned.append(cmd) or FakeProc(),
    )

    compute.start_cloud_node.callback(config_path=str(config_file), foreground=False)

    assert len(spawned) == 1
    assert "--config" in spawned[0]
    assert str(config_file) in spawned[0]


def test_start_non_systemd_without_config_path_omits_flag(monkeypatch):
    """When no --config-path is given, --config must not appear in binary args."""
    spawned: list[list[str]] = []

    class FakeProc:
        pid = 22222

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")
        binary_path = Path("/usr/bin/cyberwave-cloud-node")

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core.start_service = lambda spec: None
    fake_core.enable_and_start_service = lambda spec: None
    fake_core.write_service_override = lambda spec, config_path: True

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(compute, "_find_cloud_node_binary", lambda: "/usr/bin/cyberwave-cloud-node")
    monkeypatch.setattr(
        compute.subprocess,
        "Popen",
        lambda cmd, **_kw: spawned.append(cmd) or FakeProc(),
    )

    compute.start_cloud_node.callback(config_path=None, foreground=False)

    assert len(spawned) == 1
    assert "--config" not in spawned[0]


def test_start_systemd_with_config_path_calls_write_service_override(monkeypatch, tmp_path):
    """Under systemd, --config-path should call write_service_override before starting."""
    override_calls: list[tuple] = []
    config_file = tmp_path / "cyberwave.yml"
    config_file.write_text("cyberwave-cloud-node:\n  profile_slug: default\n")
    unit_file = tmp_path / "cyberwave-cloud-node.service"
    unit_file.write_text("[Unit]\n")

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = unit_file

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: True
    fake_core.start_service = lambda spec: True
    fake_core.write_service_override = lambda spec, config_path: override_calls.append(config_path) or True

    compute = _load_compute_module(monkeypatch, fake_core)

    compute.start_cloud_node.callback(config_path=str(config_file), foreground=False)

    assert len(override_calls) == 1
    assert str(config_file) in override_calls[0]


def test_start_systemd_write_override_failure_aborts(monkeypatch, tmp_path):
    """If write_service_override returns False, start must exit without starting."""
    started: list = []
    unit_file = tmp_path / "cyberwave-cloud-node.service"
    unit_file.write_text("[Unit]\n")

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = unit_file

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: True
    fake_core.start_service = lambda spec: started.append(True) or True
    fake_core.write_service_override = lambda spec, config_path: False  # simulate failure

    compute = _load_compute_module(monkeypatch, fake_core)

    try:
        compute.start_cloud_node.callback(config_path="/some/cyberwave.yml", foreground=False)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("Expected SystemExit(1)")

    assert started == [], "start_service must not be called when override write fails"


def test_restart_non_systemd_with_config_path_passes_flag(monkeypatch, tmp_path):
    """--config-path should append --config <path> to the binary args on restart (non-systemd)."""
    spawned: list[list[str]] = []
    config_file = tmp_path / "cyberwave.yml"
    config_file.write_text("cyberwave-cloud-node:\n  profile_slug: default\n")

    class FakeProc:
        pid = 33333

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")
        binary_path = Path("/usr/bin/cyberwave-cloud-node")
        process_match = "cyberwave-cloud-node start"
        sudo_command_hint = "sudo cyberwave compute install"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core.restart_service = lambda spec: None
    fake_core.write_service_override = lambda spec, config_path: True

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(compute, "_find_cloud_node_binary", lambda: "/usr/bin/cyberwave-cloud-node")
    monkeypatch.setattr(
        compute.subprocess,
        "run",
        lambda cmd, **_kw: type("R", (), {"stdout": "", "returncode": 0})(),
    )
    monkeypatch.setattr(
        compute.subprocess,
        "Popen",
        lambda cmd, **_kw: spawned.append(cmd) or FakeProc(),
    )

    compute.restart_cloud_node.callback(config_path=str(config_file))

    assert len(spawned) == 1
    assert "--config" in spawned[0]
    assert str(config_file) in spawned[0]


def test_install_with_config_path_calls_write_service_override(monkeypatch, tmp_path):
    """--config during install must call write_service_override before setup_service."""
    call_order: list[str] = []
    config_file = tmp_path / "cyberwave.yml"
    config_file.write_text("cyberwave-cloud-node:\n  profile_slug: default\n")

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core.write_service_override = (
        lambda spec, config_path: call_order.append("override") or True
    )
    fake_core.setup_service = (
        lambda spec, *, skip_confirm, channel, version: call_order.append("setup") or True
    )

    compute = _load_compute_module(monkeypatch, fake_core)

    compute.install_cloud_node.callback(
        yes=True, channel="stable", version=None, config_path=str(config_file)
    )

    assert call_order == ["override", "setup"], (
        "write_service_override must be called before setup_service"
    )


# ---- pgrep self-PID filter ----


def test_stop_filters_own_pid(monkeypatch):
    """stop_cloud_node must not attempt to kill the CLI process itself."""
    import signal

    killed: list[tuple[int, int]] = []
    own_pid = os.getpid()

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")
        process_match = "cyberwave-cloud-node start"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core.stop_service = lambda spec: None

    compute = _load_compute_module(monkeypatch, fake_core)

    # Simulate pgrep returning own PID alongside a real target PID
    target_pid = own_pid + 1
    monkeypatch.setattr(
        compute.subprocess,
        "run",
        lambda cmd, **_kw: type(
            "R", (), {"stdout": f"{own_pid}\n{target_pid}\n", "returncode": 0}
        )(),
    )
    monkeypatch.setattr(compute.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    compute.stop_cloud_node.callback()

    pids_killed = [pid for pid, _ in killed]
    assert own_pid not in pids_killed, "CLI must not kill itself"
    assert target_pid in pids_killed
