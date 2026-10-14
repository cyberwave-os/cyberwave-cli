"""Keep the test run away from the real machine.

The CLI computes its config directory when ``cyberwave_cli.config`` is imported, and
some code shells out to sudo, systemctl, docker, git, udevadm and friends. Both are
sealed off here, before any test module imports the package:

- the config directory and HOME point at a throwaway folder, and SUDO_USER is dropped;
- the commands above raise unless a test stubs ``subprocess`` itself. A test that really
  needs one marks itself ``allow_host_commands``; a test that only needs them to "not be
  there" uses the ``host_commands_missing`` fixture.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

_SANDBOX = Path(tempfile.mkdtemp(prefix="cw-cli-tests-"))
os.environ["HOME"] = str(_SANDBOX)
os.environ["CYBERWAVE_EDGE_CONFIG_DIR"] = str(_SANDBOX / ".cyberwave")
for _name in ("SUDO_USER", "SUDO_UID", "SUDO_GID"):
    os.environ.pop(_name, None)

_FORBIDDEN = {"sudo", "systemctl", "docker", "git", "udevadm", "apt-get", "launchctl"}


def _command_name(args) -> str:
    first = args[0] if isinstance(args, (list, tuple)) and args else args
    return os.path.basename(str(first)).split()[0] if first else ""


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "allow_host_commands: the test may run the otherwise forbidden host commands"
    )


@pytest.fixture
def host_commands_missing(monkeypatch):
    """Forbidden host commands behave as if they were not installed."""

    def run(args, *_a, **_kw):
        return subprocess.CompletedProcess(args, 127, "", f"{_command_name(args)}: not available")

    def missing(args, *_a, **_kw):
        raise FileNotFoundError(_command_name(args))

    real_run, real_popen, real_check_output = (
        subprocess.run,
        subprocess.Popen,
        subprocess.check_output,
    )

    def wrap(real, fake):
        return lambda args, *a, **kw: (
            fake(args, *a, **kw) if _command_name(args) in _FORBIDDEN else real(args, *a, **kw)
        )

    monkeypatch.setattr(subprocess, "run", wrap(real_run, run))
    monkeypatch.setattr(subprocess, "Popen", wrap(real_popen, missing))
    monkeypatch.setattr(subprocess, "check_output", wrap(real_check_output, missing))


@pytest.fixture(autouse=True)
def _no_host_commands(monkeypatch, request):
    if request.node.get_closest_marker("allow_host_commands"):
        return
    real_run, real_popen, real_check_output = (
        subprocess.run,
        subprocess.Popen,
        subprocess.check_output,
    )

    def guard(real):
        def wrapper(args, *a, **kw):
            name = _command_name(args)
            if name in _FORBIDDEN:
                raise RuntimeError(
                    f"test tried to run the host command {name!r}; stub subprocess in the test"
                )
            return real(args, *a, **kw)

        return wrapper

    monkeypatch.setattr(subprocess, "run", guard(real_run))
    monkeypatch.setattr(subprocess, "Popen", guard(real_popen))
    monkeypatch.setattr(subprocess, "check_output", guard(real_check_output))
