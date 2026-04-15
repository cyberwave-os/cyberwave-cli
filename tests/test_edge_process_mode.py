import importlib
import importlib.util
import signal
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


commands_dir = Path(__file__).resolve().parents[1] / "cyberwave_cli" / "commands"
commands_package = ModuleType("cyberwave_cli.commands")
commands_package.__path__ = [str(commands_dir)]
sys.modules.setdefault("cyberwave_cli.commands", commands_package)


def _load_edge_module():
    sys.modules.pop("cyberwave_cli.commands.edge", None)
    edge_pkg_dir = commands_dir / "edge"
    spec = importlib.util.spec_from_file_location(
        "cyberwave_cli.commands.edge",
        edge_pkg_dir / "__init__.py",
        submodule_search_locations=[str(edge_pkg_dir)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cyberwave_cli.commands.edge"] = module
    spec.loader.exec_module(module)
    return module


def _install_fake_modules(
    monkeypatch,
    *,
    env: dict[str, str] | None = None,
    has_systemd=False,
    is_macos=False,
    process_match="cyberwave-edge-core",
    resolved_binary="/tmp/cyberwave-edge-core",
    plist_path: Path | None = None,
    launchagent_label="com.cyberwave.edge.core",
):
    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: dict(env or {})

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core.SYSTEMD_UNIT_PATH = Path("/nonexistent/cyberwave-edge-core.service")
    fake_core.EDGE_CORE_SPEC = SimpleNamespace(
        process_match=process_match,
        package_name="cyberwave-edge-core",
        unit_name="cyberwave-edge-core.service",
        unit_path=Path("/nonexistent/cyberwave-edge-core.service"),
        sudo_command_hint="sudo cyberwave edge install",
        install_command_hint="cyberwave edge install",
    )
    fake_core._has_systemd = lambda: has_systemd
    fake_core._is_macos = lambda: is_macos
    fake_core._resolve_service_binary = lambda spec: resolved_binary
    fake_core._launchagent_label = lambda spec: launchagent_label
    fake_core._launchagent_plist_path = lambda spec: plist_path or Path("/nonexistent/com.cyberwave.edge.core.plist")
    fake_core._launchagent_target = lambda spec: ("gui/501", f"gui/501/{launchagent_label}")
    fake_core._launchagent_log_path = lambda spec: Path.home() / "Library" / "Logs" / "Cyberwave" / f"{launchagent_label}.log"
    fake_core.start_service = lambda spec: True
    fake_core.stop_service = lambda: None
    fake_core.restart_service = lambda: None
    fake_core.create_launchagent_service = lambda spec, *, config_path=None: True
    fake_core.load_launchagent_service = lambda spec: True

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)


def test_start_edge_foreground_uses_resolved_edge_core_binary(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("EDGE=1\n", encoding="utf-8")
    invoked: list[tuple[list[str], str, dict[str, str]]] = []

    _install_fake_modules(
        monkeypatch,
        env={"BASE": "1"},
        resolved_binary="/Users/test/.cyberwave-cli/venv-local/bin/cyberwave-edge-core",
    )
    edge_module = _load_edge_module()

    monkeypatch.setattr(
        edge_module.subprocess,
        "run",
        lambda command, **kwargs: invoked.append(
            (command, kwargs["cwd"], kwargs["env"])
        ) or SimpleNamespace(returncode=0),
    )

    edge_module.start_edge.callback(env_file=str(env_file), foreground=True)

    assert invoked == [
        (
            ["/Users/test/.cyberwave-cli/venv-local/bin/cyberwave-edge-core"],
                env_file.parent,
            {
                "BASE": "1",
                "DOTENV_PATH": str(env_file),
            },
        )
    ]


def test_stop_edge_process_mode_matches_edge_core_binary(monkeypatch):
    killed: list[tuple[int, int]] = []
    pgrep_commands: list[list[str]] = []

    _install_fake_modules(monkeypatch, has_systemd=False, process_match="cyberwave-edge-core")
    edge_module = _load_edge_module()

    def _fake_run(command, **_kwargs):
        pgrep_commands.append(command)
        return SimpleNamespace(stdout="12345\n", returncode=0)

    monkeypatch.setattr(edge_module.subprocess, "run", _fake_run)
    monkeypatch.setattr(edge_module.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    edge_module.stop_edge.callback()

    assert pgrep_commands == [["pgrep", "-f", "cyberwave-edge-core"]]
    assert killed == [(12345, signal.SIGTERM)]


def test_restart_edge_process_mode_uses_resolved_binary_and_process_match(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("EDGE=1\n", encoding="utf-8")
    resolved_binary = "/Users/test/.cyberwave-cli/venv-local/bin/cyberwave-edge-core"
    pgrep_commands: list[list[str]] = []
    killed: list[tuple[int, int]] = []
    spawned: list[tuple[list[str], str, dict[str, str]]] = []

    _install_fake_modules(
        monkeypatch,
        env={"BASE": "1"},
        has_systemd=False,
        process_match="cyberwave-edge-core",
        resolved_binary=resolved_binary,
    )
    edge_module = _load_edge_module()

    def _fake_run(command, **_kwargs):
        pgrep_commands.append(command)
        return SimpleNamespace(stdout="222\n", returncode=0)

    class _Proc:
        pid = 777

    monkeypatch.setattr(edge_module.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        edge_module.subprocess,
        "Popen",
        lambda command, **kwargs: spawned.append(
            (command, kwargs["cwd"], kwargs["env"])
        ) or _Proc(),
    )
    monkeypatch.setattr(edge_module.os, "kill", lambda pid, sig: killed.append((pid, sig)))
    monkeypatch.setattr(edge_module.time, "sleep", lambda _seconds: None)

    edge_module.restart_edge.callback(env_file=str(env_file))

    assert pgrep_commands == [["pgrep", "-f", "cyberwave-edge-core"]]
    assert killed == [(222, signal.SIGTERM)]
    assert spawned == [
        (
            [resolved_binary],
            env_file.parent,
            {
                "BASE": "1",
                "DOTENV_PATH": str(env_file),
            },
        )
    ]


def test_start_edge_background_on_macos_does_not_suggest_edge_logs(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("EDGE=1\n", encoding="utf-8")
    printed: list[str] = []

    _install_fake_modules(monkeypatch, resolved_binary="/tmp/cyberwave-edge-core")
    edge_module = _load_edge_module()

    monkeypatch.setattr(
        edge_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=999),
    )
    monkeypatch.setattr(edge_module.console, "print", lambda message="", *a, **kw: printed.append(str(message)))
    monkeypatch.setattr(edge_module.sys, "platform", "darwin")

    edge_module.start_edge.callback(env_file=str(env_file), foreground=False)

    output = "\n".join(printed)
    assert "cyberwave edge logs" not in output
    assert "start -f" in output


def test_show_logs_on_macos_shows_missing_launchagent_log_message(monkeypatch):
    printed: list[str] = []

    fake_config = ModuleType("cyberwave_cli.config")
    fake_config.clean_subprocess_env = lambda: {}

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core.EDGE_CORE_SPEC = SimpleNamespace(package_name="cyberwave-edge-core")
    fake_core.SYSTEMD_UNIT_NAME = "cyberwave-edge-core.service"
    fake_core._launchagent_label = lambda spec: "com.cyberwave.edge.core"

    monkeypatch.setitem(sys.modules, "cyberwave_cli.config", fake_config)
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    edge_module = _load_edge_module()
    monkeypatch.setattr(edge_module.sys, "platform", "darwin")
    monkeypatch.setattr(edge_module.console, "print", lambda message="", *a, **kw: printed.append(str(message)))

    edge_module.show_logs.callback(follow=False, lines=50)

    output = "\n".join(printed)
    assert "log file not found" in output.lower()
    assert "cyberwave edge install" in output
    assert "cyberwave edge start -f" in output


def test_start_edge_macos_uses_launchagent_when_installed(monkeypatch, tmp_path):
    plist_path = tmp_path / "com.cyberwave.edge.core.plist"
    plist_path.write_text("plist", encoding="utf-8")
    run_calls: list[list[str]] = []

    _install_fake_modules(monkeypatch, is_macos=True, plist_path=plist_path)
    edge_module = _load_edge_module()

    monkeypatch.setattr(edge_module.os, "getuid", lambda: 501)
    monkeypatch.setattr(
        edge_module.subprocess,
        "run",
        lambda command, **_kwargs: run_calls.append(command) or SimpleNamespace(returncode=0),
    )

    edge_module.start_edge.callback(env_file=None, foreground=False)

    assert run_calls == [["launchctl", "kickstart", "-k", "gui/501/com.cyberwave.edge.core"]]


def test_stop_edge_macos_treats_bootout_exit_3_as_not_loaded(monkeypatch, tmp_path):
    printed: list[str] = []
    plist_path = tmp_path / "com.cyberwave.edge.core.plist"
    plist_path.write_text("plist", encoding="utf-8")

    _install_fake_modules(monkeypatch, is_macos=True, plist_path=plist_path)
    edge_module = _load_edge_module()

    monkeypatch.setattr(edge_module.os, "getuid", lambda: 501)
    monkeypatch.setattr(
        edge_module.subprocess,
        "run",
        lambda command, **_kwargs: SimpleNamespace(returncode=3),
    )
    monkeypatch.setattr(edge_module.console, "print", lambda message="", *a, **kw: printed.append(str(message)))

    edge_module.stop_edge.callback()

    assert any("not loaded" in message.lower() for message in printed)


def test_status_edge_macos_shows_launchagent_loaded(monkeypatch, tmp_path):
    printed: list[str] = []
    plist_path = tmp_path / "com.cyberwave.edge.core.plist"
    plist_path.write_text("plist", encoding="utf-8")

    _install_fake_modules(monkeypatch, is_macos=True, plist_path=plist_path)
    edge_module = _load_edge_module()

    monkeypatch.setattr(edge_module.os, "getuid", lambda: 501)
    monkeypatch.setattr(
        edge_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="state = running\n"),
    )
    monkeypatch.setattr(edge_module.console, "print", lambda message="", *a, **kw: printed.append(str(message)))

    edge_module.status_edge.callback()

    assert any("launchagent" in message.lower() and "loaded" in message.lower() for message in printed)


def test_show_logs_on_macos_prints_launchagent_log_lines(monkeypatch, tmp_path):
    printed: list[str] = []
    home_dir = tmp_path / "home"
    log_dir = home_dir / "Library" / "Logs" / "Cyberwave"
    log_dir.mkdir(parents=True)
    (log_dir / "com.cyberwave.edge.core.log").write_text(
        "line1\nline2\nline3\n",
        encoding="utf-8",
    )

    _install_fake_modules(monkeypatch, is_macos=True)
    edge_module = _load_edge_module()

    monkeypatch.setattr(edge_module.Path, "home", staticmethod(lambda: home_dir))
    monkeypatch.setattr(edge_module.sys, "platform", "darwin")
    monkeypatch.setattr(edge_module.console, "print", lambda message="", *a, **kw: printed.append(str(message)))

    edge_module.show_logs.callback(follow=False, lines=2)

    output = "\n".join(printed)
    assert "line2" in output
    assert "line3" in output
    assert "line1" not in output
