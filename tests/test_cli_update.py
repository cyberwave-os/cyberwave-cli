"""``cyberwave update`` (CYB-4419)."""

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest
from click.testing import CliRunner
from packaging.version import Version

from tests._core_module_loader import load_core_module
from tests._fake_apt import FakeApt


def _load_update_command():
    """The ``update`` command, loaded from its file so that no test file imports the
    ``cyberwave_cli.commands`` package: which copy of it other tests see depends on
    who imports it first (see test_pair_alias)."""
    package_root = Path(__file__).resolve().parents[1] / "cyberwave_cli"
    spec = importlib.util.spec_from_file_location(
        "cyberwave_cli.commands.update", package_root / "commands" / "update.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.update


@pytest.fixture(autouse=True)
def _restore_cyberwave_cli_modules():
    """``load_core_module`` swaps a stub ``cyberwave_cli`` package into ``sys.modules``
    for good; put the real modules back so these tests leave no trace for later ones."""

    def snapshot():
        return {
            name: module
            for name, module in sys.modules.items()
            if name == "cyberwave_cli" or name.startswith("cyberwave_cli.")
        }

    saved = snapshot()
    yield
    for name in snapshot():
        sys.modules.pop(name, None)
    sys.modules.update(saved)


def _apt_setup(monkeypatch, apt, *, root=True, new_version="0.12.9", verified=True):
    """Run ``update_cli`` as the frozen Debian-package binary."""
    core = load_core_module(monkeypatch)
    events: list[tuple] = []

    def fake_run(cmd, **_kwargs):
        events.append(("run", cmd))
        if cmd[:2] == ["apt-get", "install"]:
            package, _, version = cmd[-1].partition("=")
            apt.installed[package] = version or new_version

    monkeypatch.setattr(core.sys, "frozen", True, raising=False)
    monkeypatch.setattr(core.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(core.os, "geteuid", lambda: 0 if root else 1000)
    monkeypatch.setattr(core.subprocess, "run", apt.run)
    monkeypatch.setattr(core, "_run", fake_run)
    monkeypatch.setattr(core, "_apt_get_update", lambda env: events.append(("apt-update",)))
    monkeypatch.setattr(core, "_verify_cli_install", lambda: events.append(("verify",)) or verified)
    return core, events


def _python_setup(monkeypatch, *, channel="stable", version="0.12.8", verified=True, token=None):
    """Run ``update_cli`` as a wheel in a virtualenv, with the index and pip mocked."""
    core = load_core_module(monkeypatch)
    events: list[tuple] = []
    monkeypatch.setattr(
        core,
        "_detect_cli_install",
        lambda: core.InstalledCli("python", "cyberwave-cli", version, channel, "pipx"),
    )
    monkeypatch.setattr(core, "_internal_python_read_token", lambda: token)
    monkeypatch.setattr(core, "_run", lambda cmd, **kw: events.append(("run", cmd)))
    monkeypatch.setattr(core, "_verify_cli_install", lambda: events.append(("verify",)) or verified)
    monkeypatch.setattr(core, "_installed_service_package", lambda spec=None: None)
    return core, events


def _index(core, monkeypatch, *versions):
    monkeypatch.setattr(
        core,
        "_fetch_available_simple_index_versions",
        lambda *args, **kwargs: [Version(v) for v in versions],
    )


# ---- detection ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "channel"),
    [
        ("0.12.8", "stable"),
        ("0.12.9.dev4", "dev"),
        ("0.12.9rc4", "staging"),
        ("0.12.9rc4+gabc123def456", "pr"),
    ],
)
def test_pip_channel_is_inferred_from_the_version(monkeypatch, version, channel):
    core = load_core_module(monkeypatch)
    assert core._infer_pip_channel(version) == channel


def test_frozen_binary_without_a_package_is_standalone(monkeypatch):
    core, _ = _apt_setup(monkeypatch, FakeApt())
    assert core._detect_cli_install().method == "standalone"
    assert core.update_cli() is False


def test_frozen_binary_with_a_package_is_apt(monkeypatch):
    core, _ = _apt_setup(monkeypatch, FakeApt(installed={"cyberwave-cli-staging": "0.12.8"}))
    installed = core._detect_cli_install()
    assert (installed.method, installed.package, installed.channel) == (
        "apt",
        "cyberwave-cli-staging",
        "staging",
    )


def test_editable_install_is_refused(monkeypatch):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core.sys, "frozen", False, raising=False)
    monkeypatch.setattr(core, "_is_editable_install", lambda: True)
    assert core.update_cli() is False


@pytest.mark.parametrize("frozen", [True, False])
def test_verification_runs_the_new_cli_in_a_fresh_process(monkeypatch, frozen):
    core = load_core_module(monkeypatch)
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs["env"]))
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(core.sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(core.subprocess, "run", fake_run)

    assert core._verify_cli_install() is True

    assert [cmd[-1] for cmd, _ in calls] == ["__selfcheck_sdk"]
    for cmd, env in calls:
        assert (cmd[:3] == [sys.executable, "-m", "cyberwave_cli.main"]) is (not frozen)
        # A frozen binary must not reuse the extraction dir of the process that starts it.
        assert (env.get("PYINSTALLER_RESET_ENVIRONMENT") == "1") is frozen


def test_verification_falls_back_to_version_for_a_cli_without_the_self_check(monkeypatch):
    core = load_core_module(monkeypatch)
    calls: list[str] = []

    def fake_run(cmd, **_kwargs):
        calls.append(cmd[-1])
        # A CLI that predates __selfcheck_sdk: click's usage error for an unknown command.
        code = 2 if cmd[-1] == "__selfcheck_sdk" else 0
        return types.SimpleNamespace(returncode=code, stdout="", stderr="")

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    assert core._verify_cli_install() is True
    assert calls == ["__selfcheck_sdk", "--version"]


def test_verification_fails_when_the_cli_does_not_start(monkeypatch):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(
        core.subprocess,
        "run",
        lambda cmd, **_kw: types.SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )

    assert core._verify_cli_install() is False


def test_run_does_not_echo_registry_credentials(monkeypatch):
    core = load_core_module(monkeypatch)
    printed: list[str] = []
    monkeypatch.setattr(core.console, "print", lambda msg="", *a, **kw: printed.append(str(msg)))
    monkeypatch.setattr(core.subprocess, "run", lambda cmd, **kw: None)

    core._run(
        [
            "pip",
            "install",
            "--extra-index-url",
            "https://buildkite:s3cr3t-token@packages.buildkite.com/cyberwave/x/pypi/simple",
        ]
    )

    assert printed and "s3cr3t-token" not in printed[0]
    assert "https://buildkite:***@packages.buildkite.com" in printed[0]


@pytest.mark.parametrize(
    ("prefix", "label"),
    [
        ("/home/u/.local/share/pipx/venvs/cyberwave-cli", "pipx"),
        ("/home/u/.cyberwave-cli/venv", "installer venv"),
        ("/opt/somewhere/venv", "pip"),
    ],
)
def test_python_environment_label(monkeypatch, prefix, label):
    core = load_core_module(monkeypatch)
    monkeypatch.delenv("CYBERWAVE_INSTALL_PYTHON_ENV_DIR", raising=False)
    monkeypatch.setattr(core.Path, "home", staticmethod(lambda: core.Path("/home/u")))
    monkeypatch.setattr(core.sys, "prefix", prefix)

    assert core._python_environment_label() == label


# ---- apt ---------------------------------------------------------------------


def test_apt_updates_the_installed_channel_and_verifies(monkeypatch):
    apt = FakeApt(installed={"cyberwave-cli-dev": "0.12.8"})
    core, events = _apt_setup(monkeypatch, apt)

    assert core.update_cli() is True

    assert events == [
        ("apt-update",),
        ("run", ["apt-get", "install", "-y", "--only-upgrade", "cyberwave-cli-dev"]),
        ("verify",),
    ]


def test_apt_already_current_skips_verification(monkeypatch):
    apt = FakeApt(installed={"cyberwave-cli": "0.12.8"})
    core, events = _apt_setup(monkeypatch, apt, new_version="0.12.8")

    assert core.update_cli() is True

    assert ("verify",) not in events


def test_apt_explicit_version_allows_downgrade(monkeypatch):
    apt = FakeApt(installed={"cyberwave-cli": "0.12.8"})
    core, events = _apt_setup(monkeypatch, apt)

    assert core.update_cli(version="0.12.7") is True

    assert events[1] == (
        "run",
        ["apt-get", "install", "-y", "--allow-downgrades", "cyberwave-cli=0.12.7"],
    )


def test_apt_held_package_is_refused_without_force(monkeypatch):
    apt = FakeApt(installed={"cyberwave-cli": "0.12.8"}, held={"cyberwave-cli"})
    core, events = _apt_setup(monkeypatch, apt)

    assert core.update_cli() is False
    assert events == []

    assert core.update_cli(force=True) is True
    assert "--allow-change-held-packages" in events[1][1]


def test_apt_pr_channel_needs_an_explicit_version(monkeypatch):
    apt = FakeApt(installed={"cyberwave-cli-pr": "0.12.9~pr1.gabc"})
    core, events = _apt_setup(monkeypatch, apt)

    assert core.update_cli() is False
    assert events == []


def test_apt_requires_root(monkeypatch):
    apt = FakeApt(installed={"cyberwave-cli": "0.12.8"})
    core, events = _apt_setup(monkeypatch, apt, root=False)

    with pytest.raises(SystemExit):
        core.update_cli()
    assert events == []


def test_apt_failed_verification_reports_failure(monkeypatch):
    apt = FakeApt(installed={"cyberwave-cli": "0.12.8"})
    core, _ = _apt_setup(monkeypatch, apt, verified=False)

    assert core.update_cli() is False


def test_apt_check_changes_nothing(monkeypatch):
    apt = FakeApt(installed={"cyberwave-cli": "0.12.8"}, candidate="0.12.9")
    core, events = _apt_setup(monkeypatch, apt, root=False)

    assert core.update_cli(check=True) is True

    assert events == []
    assert ["apt-cache", "policy", "cyberwave-cli"] in apt.calls


# ---- pip ---------------------------------------------------------------------


def test_pip_stable_installs_the_latest_and_upgrades_the_sdk(monkeypatch):
    core, events = _python_setup(monkeypatch)
    _index(core, monkeypatch, "0.12.7", "0.12.9", "0.13.0rc1", "0.12.10.dev2")

    assert core.update_cli() is True

    assert events == [
        (
            "run",
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                "cyberwave-cli==0.12.9",
                "cyberwave",
            ],
        ),
        ("verify",),
    ]


def test_pip_does_not_downgrade_a_build_ahead_of_the_index(monkeypatch):
    core, events = _python_setup(monkeypatch, version="0.12.8")
    _index(core, monkeypatch, "0.12.6", "0.12.7")

    assert core.update_cli() is True

    assert events == []


def test_pip_already_at_the_latest_installs_nothing(monkeypatch):
    core, events = _python_setup(monkeypatch, version="0.12.7")
    _index(core, monkeypatch, "0.12.7")

    assert core.update_cli() is True

    assert events == []


def test_pip_explicit_older_version_still_downgrades(monkeypatch):
    core, events = _python_setup(monkeypatch, version="0.12.8")

    assert core.update_cli(version="0.12.7") is True

    assert events[0][1][-1] == "cyberwave-cli==0.12.7"


def test_pip_update_with_an_unparsable_installed_version_proceeds(monkeypatch):
    core, events = _python_setup(monkeypatch, version="not-a-version")
    _index(core, monkeypatch, "0.12.9")

    assert core.update_cli() is True

    assert events[0][1][-2:] == ["cyberwave-cli==0.12.9", "cyberwave"]


def test_pip_explicit_version_leaves_the_sdk_alone(monkeypatch):
    core, events = _python_setup(monkeypatch)

    assert core.update_cli(version="0.12.7") is True

    assert events[0] == (
        "run",
        [sys.executable, "-m", "pip", "install", "--upgrade", "cyberwave-cli==0.12.7"],
    )


def test_pip_dev_channel_needs_a_registry_token(monkeypatch):
    core, events = _python_setup(monkeypatch, channel="dev", version="0.12.9.dev1")
    _index(core, monkeypatch, "0.12.9.dev2")

    assert core.update_cli() is False
    assert events == []


def test_pip_dev_channel_uses_the_internal_registry(monkeypatch):
    core, events = _python_setup(monkeypatch, channel="dev", version="0.12.9.dev1", token="secret")
    _index(core, monkeypatch, "0.12.9.dev1", "0.12.9.dev2")

    assert core.update_cli() is True

    command = events[0][1]
    assert command[-1] == "cyberwave-cli==0.12.9.dev2"
    assert "--pre" in command
    assert any("cyberwave-internal-python" in part for part in command)
    assert "cyberwave" not in command  # the SDK is only forced on stable


def test_pip_pr_channel_needs_an_explicit_version(monkeypatch):
    core, events = _python_setup(
        monkeypatch, channel="pr", version="0.12.9rc1+gabc123def456", token="secret"
    )

    assert core.update_cli() is False
    assert events == []


def test_pip_failed_verification_rolls_back(monkeypatch):
    core, events = _python_setup(monkeypatch, verified=False)
    _index(core, monkeypatch, "0.12.9")

    assert core.update_cli() is False

    installs = [event[1][-2:] for event in events if event[0] == "run"]
    assert installs[-1][-1] == "cyberwave-cli==0.12.8"


def test_pip_failed_rollback_says_how_to_restore(monkeypatch):
    core, _ = _python_setup(monkeypatch, verified=False)
    _index(core, monkeypatch, "0.12.9")
    # The update installs, the check fails, and the old version is not on the index.
    installs = iter([True, False])
    monkeypatch.setattr(core, "_pip_install_cli", lambda targets, **kw: next(installs))
    printed: list[str] = []
    monkeypatch.setattr(core.console, "print", lambda msg="", *a, **kw: printed.append(str(msg)))

    assert core.update_cli() is False

    restore = [m for m in printed if "Could not restore 0.12.8" in m]
    assert restore and "pip install cyberwave-cli==0.12.8" in restore[0]


def test_pip_check_changes_nothing(monkeypatch):
    core, events = _python_setup(monkeypatch)
    _index(core, monkeypatch, "0.12.9")

    assert core.update_cli(check=True) is True

    assert events == []


def test_pip_check_on_pr_channel_still_reports(monkeypatch):
    core, events = _python_setup(
        monkeypatch, channel="pr", version="0.12.9rc1+gabc123def456", token="secret"
    )

    assert core.update_cli(check=True) is True
    assert events == []


# ---- command -----------------------------------------------------------------


def test_update_command_forwards_options(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(update_cli=lambda **kwargs: calls.append(kwargs) or True),
    )

    result = CliRunner().invoke(_load_update_command(), ["--version", "0.12.7", "--force"])

    assert result.exit_code == 0, result.output
    assert calls == [{"version": "0.12.7", "check": False, "force": True}]


def test_update_command_exits_nonzero_on_failure(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(update_cli=lambda **kwargs: False),
    )

    result = CliRunner().invoke(_load_update_command(), ["--check"])

    assert result.exit_code == 1
