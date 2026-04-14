import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType

# --- stub rich (same pattern as test_edge_uninstall.py) ---
rich_module = ModuleType("rich")
rich_console_module = ModuleType("rich.console")
rich_markup_module = ModuleType("rich.markup")
rich_prompt_module = ModuleType("rich.prompt")
rich_table_module = ModuleType("rich.table")
click_module = ModuleType("click")


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
rich_markup_module.escape = lambda value: value
rich_prompt_module.Prompt = _Prompt
rich_prompt_module.Confirm = _Confirm
rich_table_module.Table = _Table
rich_module.console = rich_console_module
rich_module.markup = rich_markup_module
rich_module.prompt = rich_prompt_module
rich_module.table = rich_table_module


class _Choice:
    def __init__(self, *_args, **_kwargs):
        pass


def _path_type(*_args, **_kwargs):
    return str


def _option(*_args, **_kwargs):
    def decorator(func):
        return func

    return decorator


def _group(*_args, **_kwargs):
    def decorator(func):
        def command(*_cmd_args, **_cmd_kwargs):
            def inner(cmd_func):
                cmd_func.callback = cmd_func
                return cmd_func

            return inner

        func.command = command
        return func

    return decorator


click_module.Choice = _Choice
click_module.Path = _path_type
click_module.option = _option
click_module.group = _group

sys.modules.setdefault("rich", rich_module)
sys.modules.setdefault("rich.console", rich_console_module)
sys.modules.setdefault("rich.markup", rich_markup_module)
sys.modules.setdefault("rich.prompt", rich_prompt_module)
sys.modules.setdefault("rich.table", rich_table_module)
sys.modules.setdefault("click", click_module)

# --- load compute module ---
commands_dir = Path(__file__).resolve().parents[1] / "cyberwave_cli" / "commands"
commands_package = ModuleType("cyberwave_cli.commands")
commands_package.__path__ = [str(commands_dir)]
sys.modules.setdefault("cyberwave_cli.commands", commands_package)


def _load_compute_module(monkeypatch, fake_core=None, fake_config=None):
    fake_core = fake_core or ModuleType("cyberwave_cli.core")
    fake_config = fake_config or ModuleType("cyberwave_cli.config")
    if not hasattr(fake_config, "CONFIG_DIR"):
        fake_config.CONFIG_DIR = Path("/tmp/cyberwave-config")
    if not hasattr(fake_config, "CREDENTIALS_FILE"):
        fake_config.CREDENTIALS_FILE = fake_config.CONFIG_DIR / "credentials.json"
    if not hasattr(fake_config, "chown_to_sudo_user"):
        fake_config.chown_to_sudo_user = lambda *args, **kwargs: None
    fake_config.clean_subprocess_env = lambda: {}
    if not hasattr(fake_config, "get_api_url"):
        fake_config.get_api_url = lambda: "https://api.example.test"
    if not hasattr(fake_core, "_has_systemd"):
        fake_core._has_systemd = lambda: True
    if not hasattr(fake_core, "_is_macos"):
        fake_core._is_macos = lambda: False
    if not hasattr(fake_core, "_launchagent_label"):
        fake_core._launchagent_label = lambda spec: "com.cyberwave.cloud-node"
    if not hasattr(fake_core, "_launchagent_target"):
        fake_core._launchagent_target = lambda spec: (
            "gui/501",
            f"gui/501/{fake_core._launchagent_label(spec)}",
        )
    if not hasattr(fake_core, "_launchagent_log_path"):
        fake_core._launchagent_log_path = lambda spec: Path.home() / "Library" / "Logs" / "Cyberwave" / f"{fake_core._launchagent_label(spec)}.log"
    if not hasattr(fake_core, "create_launchagent_service"):
        fake_core.create_launchagent_service = lambda spec, config_path=None: True
    if not hasattr(fake_core, "load_launchagent_service"):
        fake_core.load_launchagent_service = lambda spec: True
    fake_credentials = sys.modules.get("cyberwave_cli.credentials")
    if fake_credentials is None:
        fake_credentials = ModuleType("cyberwave_cli.credentials")
    if not hasattr(fake_credentials, "Credentials"):
        fake_credentials.Credentials = type("Credentials", (), {})
    if not hasattr(fake_credentials, "load_credentials"):
        fake_credentials.load_credentials = lambda: None
    if not hasattr(fake_credentials, "save_credentials"):
        fake_credentials.save_credentials = lambda *_args, **_kwargs: None
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", fake_credentials)
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
    fake_core.setup_service = (
        lambda spec, *, skip_confirm, channel, version, config_path=None: calls.append(
            (skip_confirm, channel, version, config_path)
        )
        or True
    )
    fake_core.write_service_override = lambda spec, config_path: True

    compute = _load_compute_module(monkeypatch, fake_core)
    compute.install_cloud_node.callback(yes=True, channel="stable", version=None, config_path=None)

    assert calls == [(True, "stable", None, None)]


def test_install_calls_setup_service_for_nonstable_channel(monkeypatch):
    calls = []

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core.setup_service = (
        lambda spec, *, skip_confirm, channel, version, config_path=None: calls.append(
            (skip_confirm, channel, version, config_path)
        )
        or True
    )
    fake_core.write_service_override = lambda spec, config_path: True

    compute = _load_compute_module(monkeypatch, fake_core)
    compute.install_cloud_node.callback(
        yes=True,
        channel="dev",
        version="0.3.1.dev8",
        config_path=None,
    )

    assert calls == [(True, "dev", "0.3.1.dev8", None)]


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


def test_stop_macos_treats_bootout_exit_3_as_not_loaded(monkeypatch, tmp_path):
    printed: list[str] = []
    plist_path = tmp_path / "com.cyberwave.cloud-node.plist"
    plist_path.write_text("plist", encoding="utf-8")

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core._is_macos = lambda: True
    fake_core.stop_service = lambda spec: None
    fake_core._launchagent_plist_path = lambda spec: plist_path
    fake_core._launchagent_label = lambda spec: "com.cyberwave.cloud-node"

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(
        compute.subprocess,
        "run",
        lambda cmd, **_kw: type("R", (), {"returncode": 3})(),
    )
    monkeypatch.setattr(compute.console, "print", lambda msg="", *a, **kw: printed.append(str(msg)))

    compute.stop_cloud_node.callback()

    assert any("not loaded" in message.lower() for message in printed)


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
    fake_core._is_macos = lambda: False
    fake_core.create_launchagent_service = lambda spec, config_path=None: True
    fake_core.load_launchagent_service = lambda spec: True
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


def test_start_foreground_uses_resolved_service_binary(monkeypatch, tmp_path):
    invoked: list[list[str]] = []
    resolved_binary = tmp_path / "venv-local" / "bin" / "cyberwave-cloud-node"
    resolved_binary.parent.mkdir(parents=True, exist_ok=True)
    resolved_binary.write_text("#!/bin/sh\n", encoding="utf-8")

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")
        binary_path = Path("/usr/bin/cyberwave-cloud-node")
        sudo_command_hint = "cyberwave compute install"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core._is_macos = lambda: False
    fake_core._resolve_service_binary = lambda spec: str(resolved_binary)
    fake_core.create_launchagent_service = lambda spec, config_path=None: True
    fake_core.load_launchagent_service = lambda spec: True
    fake_core.start_service = lambda spec: None
    fake_core.enable_and_start_service = lambda spec: None
    fake_core.write_service_override = lambda spec, config_path: True

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(
        compute.subprocess,
        "run",
        lambda cmd, **_kw: invoked.append(cmd) or type("R", (), {"returncode": 0})(),
    )

    compute.start_cloud_node.callback(config_path=None, foreground=True)

    assert invoked == [[str(resolved_binary)]]


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


def test_status_macos_shows_launchagent_loaded(monkeypatch):
    printed: list[str] = []

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._is_macos = lambda: True
    fake_core._launchagent_label = lambda spec: "com.cyberwave.cloud-node"

    compute = _load_compute_module(monkeypatch, fake_core)
    monkeypatch.setattr(compute.os, "getuid", lambda: 501)
    monkeypatch.setattr(
        compute.subprocess,
        "run",
        lambda *a, **kw: type("R", (), {"returncode": 0, "stdout": "state = running\n"})(),
    )
    monkeypatch.setattr(compute.console, "print", lambda msg="", *a, **kw: printed.append(str(msg)))

    compute.status_cloud_node.callback()

    assert any("launchagent" in message.lower() and "loaded" in message.lower() for message in printed)


def test_logs_macos_shows_missing_log_file_message(monkeypatch, tmp_path):
    printed: list[str] = []
    home_dir = tmp_path / "home"
    home_dir.mkdir()

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._launchagent_label = lambda spec: "com.cyberwave.cloud-node"

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(compute.Path, "home", staticmethod(lambda: home_dir))
    monkeypatch.setattr(compute.sys, "platform", "darwin")
    monkeypatch.setattr(compute.console, "print", lambda msg="", *a, **kw: printed.append(str(msg)))

    compute.logs_cloud_node.callback(follow=False, lines=5)

    assert any("log file" in message.lower() and "not found" in message.lower() for message in printed)


def test_logs_macos_prints_last_n_lines(monkeypatch, tmp_path):
    printed: list[str] = []
    home_dir = tmp_path / "home"
    log_dir = home_dir / "Library" / "Logs" / "Cyberwave"
    log_dir.mkdir(parents=True)
    (log_dir / "com.cyberwave.cloud-node.log").write_text(
        "line1\nline2\nline3\nline4\n",
        encoding="utf-8",
    )

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._launchagent_label = lambda spec: "com.cyberwave.cloud-node"

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(compute.Path, "home", staticmethod(lambda: home_dir))
    monkeypatch.setattr(compute.sys, "platform", "darwin")
    monkeypatch.setattr(compute.console, "print", lambda msg="", *a, **kw: printed.append(str(msg)))

    compute.logs_cloud_node.callback(follow=False, lines=2)

    output = "\n".join(printed)
    assert "line3" in output
    assert "line4" in output
    assert "line1" not in output


def test_logs_macos_follow_streams_new_lines(monkeypatch, tmp_path):
    printed: list[str] = []
    home_dir = tmp_path / "home"
    log_dir = home_dir / "Library" / "Logs" / "Cyberwave"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "com.cyberwave.cloud-node.log"
    log_file.write_text("existing\n", encoding="utf-8")

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._launchagent_label = lambda spec: "com.cyberwave.cloud-node"

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(compute.Path, "home", staticmethod(lambda: home_dir))
    monkeypatch.setattr(compute.sys, "platform", "darwin")
    monkeypatch.setattr(compute.console, "print", lambda msg="", *a, **kw: printed.append(str(msg)))

    sleep_calls = {"count": 0}

    def fake_sleep(_seconds):
        sleep_calls["count"] += 1
        if sleep_calls["count"] == 1:
            with log_file.open("a", encoding="utf-8") as handle:
                handle.write("new line\n")
                handle.flush()
            return
        raise KeyboardInterrupt

    monkeypatch.setattr(compute.time, "sleep", fake_sleep)

    compute.logs_cloud_node.callback(follow=True, lines=1)

    output = "\n".join(printed)
    assert "existing" in output
    assert "new line" in output


# ---- macOS uninstall ----


def test_uninstall_macos_removes_launchagent_and_uninstalls_package(monkeypatch, tmp_path):
    home_dir = tmp_path / "home"
    plist_path = home_dir / "Library" / "LaunchAgents" / "com.cyberwave.cloud-node.plist"
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text("plist", encoding="utf-8")

    calls: list[list[str]] = []

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = tmp_path / "nonexistent.service"
        sudo_command_hint = "cyberwave compute install"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._is_macos = lambda: True
    fake_core._resolve_installed_service_package_name = lambda spec: "cyberwave-cloud-node"
    fake_core.clear_service_override = lambda spec: None
    fake_core._launchagent_plist_path = lambda spec: plist_path
    fake_core._launchagent_label = lambda spec: "com.cyberwave.cloud-node"

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(compute.os, "getuid", lambda: 501)

    def fake_run(cmd, **_kw):
        calls.append(cmd)
        if cmd[:2] == ["launchctl", "bootout"]:
            return type("R", (), {"returncode": 0})()
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(compute.subprocess, "run", fake_run)

    compute.uninstall_cloud_node.callback(yes=True)

    assert not plist_path.exists()
    assert ["launchctl", "bootout", "gui/501/com.cyberwave.cloud-node"] in calls
    assert [compute.sys.executable, "-m", "pip", "uninstall", "-y", "cyberwave-cloud-node"] in calls


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


def test_start_non_systemd_passes_runtime_envs_from_stored_credentials(monkeypatch):
    spawned_envs: list[dict[str, str]] = []

    class FakeProc:
        pid = 33333

    class FakeCredentials:
        def runtime_envs(self):
            return {
                "CYBERWAVE_BASE_URL": "http://localhost:8000",
                "CYBERWAVE_MQTT_HOST": "localhost",
                "CYBERWAVE_MQTT_PORT": "1883",
            }

    fake_core = ModuleType("cyberwave_cli.core")

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"
        unit_path = Path("/nonexistent/unit")
        binary_path = Path("/usr/bin/cyberwave-cloud-node")

    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core.start_service = lambda spec: None
    fake_core.enable_and_start_service = lambda spec: None
    fake_core.write_service_override = lambda spec, config_path: True

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {"PATH": "/usr/bin"}

    fake_credentials = ModuleType("cyberwave_cli.credentials")
    fake_credentials.load_credentials = lambda: FakeCredentials()

    monkeypatch.setitem(sys.modules, "cyberwave_cli.credentials", fake_credentials)

    compute = _load_compute_module(monkeypatch, fake_core, fake_config)
    monkeypatch.setattr(compute, "_find_cloud_node_binary", lambda: "/usr/bin/cyberwave-cloud-node")
    monkeypatch.setattr(
        compute.subprocess,
        "Popen",
        lambda cmd, **kw: spawned_envs.append(kw["env"]) or FakeProc(),
    )

    compute.start_cloud_node.callback(config_path=None, foreground=False)

    assert len(spawned_envs) == 1
    assert spawned_envs[0]["CYBERWAVE_BASE_URL"] == "http://localhost:8000"
    assert spawned_envs[0]["CYBERWAVE_MQTT_HOST"] == "localhost"
    assert spawned_envs[0]["CYBERWAVE_MQTT_PORT"] == "1883"


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
    fake_core.write_service_override = (
        lambda spec, config_path: override_calls.append(config_path) or True
    )

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
        lambda spec, *, skip_confirm, channel, version, config_path=None: call_order.append(
            f"setup:{config_path}"
        ) or True
    )

    compute = _load_compute_module(monkeypatch, fake_core)

    compute.install_cloud_node.callback(
        yes=True, channel="stable", version=None, config_path=str(config_file)
    )

    assert call_order == ["override", f"setup:{config_file}"], (
        "write_service_override must be called before setup_service"
    )


def test_install_non_systemd_with_config_path_skips_write_service_override(
    monkeypatch, tmp_path
):
    """Non-systemd installs must not try to write a systemd override."""
    calls: list[str] = []
    config_file = tmp_path / "cyberwave.yml"
    config_file.write_text("cyberwave-cloud-node:\n  profile_slug: default\n")

    class FakeSpec:
        package_name = "cyberwave-cloud-node"
        unit_name = "cyberwave-cloud-node.service"

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.CLOUD_NODE_SPEC = FakeSpec()
    fake_core._has_systemd = lambda: False
    fake_core.write_service_override = (
        lambda spec, config_path: calls.append("override") or True
    )
    fake_core.setup_service = (
        lambda spec, *, skip_confirm, channel, version, config_path=None: calls.append(
            f"setup:{config_path}"
        ) or True
    )

    compute = _load_compute_module(monkeypatch, fake_core)

    compute.install_cloud_node.callback(
        yes=True, channel="stable", version=None, config_path=str(config_file)
    )

    assert calls == [f"setup:{config_file}"]


# ---- pgrep self-PID filter ----


def test_stop_filters_own_pid(monkeypatch):
    """stop_cloud_node must not attempt to kill the CLI process itself."""
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
