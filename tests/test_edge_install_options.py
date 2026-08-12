import importlib
import sys
import types

import pytest
from click.testing import CliRunner

from tests._core_module_loader import load_core_module

_edge_module = importlib.import_module("cyberwave_cli.commands.edge")

# Bound before tests stub out `cyberwave_cli.core`; a lazy lookup would resolve
# back to the stub and recurse.
from cyberwave_cli.core import (  # noqa: E402
    normalize_environment_reference as _real_normalize_environment_reference,
)


def test_install_edge_core_defaults_to_stable_package(monkeypatch):
    core = load_core_module(monkeypatch)
    calls = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(
        core.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None
    )
    monkeypatch.setattr(
        core,
        "_apt_get_install",
        lambda spec, *, package_name, package_version: (
            calls.append((package_name, package_version)) or True
        ),
    )

    assert core.install_edge_core() is True
    assert calls == [("cyberwave-edge-core", None)]


def test_install_edge_core_uses_selected_channel_and_version(monkeypatch):
    core = load_core_module(monkeypatch)
    calls = []

    monkeypatch.setattr(core, "_is_linux", lambda: True)
    monkeypatch.setattr(
        core.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None
    )
    monkeypatch.setattr(
        core,
        "_apt_get_install",
        lambda spec, *, package_name, package_version: (
            calls.append((package_name, package_version)) or True
        ),
    )

    assert (
        core.install_edge_core(channel="staging", version="0.0.42.595") is True
    )
    assert calls == [("cyberwave-edge-core-staging", "0.0.42.595")]


def test_install_edge_core_uses_pip_for_nonstable_channel_without_apt(monkeypatch):
    core = load_core_module(monkeypatch)
    calls = []

    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(
        core,
        "_pip_install",
        lambda spec, *, package_version=None, channel="stable": (
            calls.append((spec.package_name, package_version, channel)) or True
        ),
    )

    assert core.install_edge_core(channel="dev", version="0.3.1.dev5") is True
    assert calls == [("cyberwave-edge-core", "0.3.1.dev5", "dev")]


def test_edge_install_command_uses_channel_flag(monkeypatch):
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
            normalize_environment_reference=_real_normalize_environment_reference,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        _edge_module.install_edge,
        ["--channel", "dev", "--version", "0.3.1.dev5", "--yes"],
    )

    assert result.exit_code == 0
    assert calls == [
        {
            "skip_confirm": True,
            "channel": "dev",
            "version": "0.3.1.dev5",
            "force_reinstall": False,
            "environment": None,
            "token": None,
        }
    ]


def test_edge_install_without_workers_flag_is_deprecated_noop(monkeypatch):
    """``--without-workers`` is accepted for backward compatibility but does not
    forward ``pull_worker_image`` to ``setup_edge_core``."""
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
            normalize_environment_reference=_real_normalize_environment_reference,
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        _edge_module.install_edge,
        ["--without-workers", "--yes"],
    )

    assert result.exit_code == 0
    assert "deprecated" in result.output.lower()
    assert calls == [
        {
            "skip_confirm": True,
            "channel": "stable",
            "version": None,
            "force_reinstall": False,
            "environment": None,
            "token": None,
        }
    ]


def test_edge_install_command_rejects_old_edge_core_channel_flag():
    runner = CliRunner()
    result = runner.invoke(_edge_module.install_edge, ["--edge-core-channel", "dev", "--yes"])

    assert result.exit_code != 0
    assert "No such option: --edge-core-channel" in result.output


def test_edge_install_command_rejects_old_edge_core_version_flag():
    runner = CliRunner()
    result = runner.invoke(_edge_module.install_edge, ["--edge-core-version", "0.3.1.dev5", "--yes"])

    assert result.exit_code != 0
    assert "No such option: --edge-core-version" in result.output


def test_edge_install_forwards_environment_flag(monkeypatch):
    """``-e/--environment`` reaches ``setup_edge_core`` verbatim (CYB-3213)."""
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
            normalize_environment_reference=_real_normalize_environment_reference,
        ),
    )

    result = CliRunner().invoke(
        _edge_module.install_edge,
        ["-e", "acme/envs/production-floor", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["environment"] == "acme/envs/production-floor"


def test_edge_install_environment_flag_has_a_long_form(monkeypatch):
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
            normalize_environment_reference=_real_normalize_environment_reference,
        ),
    )

    result = CliRunner().invoke(
        _edge_module.install_edge,
        ["--environment", "11111111-2222-3333-4444-555555555555", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["environment"] == "11111111-2222-3333-4444-555555555555"


def test_edge_install_rejects_malformed_environment_before_installing_anything(monkeypatch):
    """A typo must fail at parse time, not after apt-get."""
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
            normalize_environment_reference=_real_normalize_environment_reference,
        ),
    )

    result = CliRunner().invoke(_edge_module.install_edge, ["-e", "production-floor", "--yes"])

    assert result.exit_code == 2, result.output
    assert "envs" in result.output, result.output
    assert calls == [], "setup_edge_core must not run for a malformed --environment"


def test_edge_install_rejects_empty_environment_instead_of_guessing(monkeypatch):
    """``-e "$UNSET_VAR"`` must not degrade into "no -e given"."""
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
            normalize_environment_reference=_real_normalize_environment_reference,
        ),
    )

    result = CliRunner().invoke(_edge_module.install_edge, ["-e", "", "--yes"])

    assert result.exit_code == 2, result.output
    assert calls == []


def test_edge_install_strips_whitespace_around_environment(monkeypatch):
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
            normalize_environment_reference=_real_normalize_environment_reference,
        ),
    )

    result = CliRunner().invoke(
        _edge_module.install_edge, ["-e", "  acme/envs/production-floor  ", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["environment"] == "acme/envs/production-floor"


def test_edge_install_rejects_a_three_part_slug_for_another_entity(monkeypatch):
    """Twins and workflows share the slug shape, so a pasted twin URL looks valid."""
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
            normalize_environment_reference=_real_normalize_environment_reference,
        ),
    )

    result = CliRunner().invoke(
        _edge_module.install_edge, ["-e", "acme/twins/arm-station-1", "--yes"]
    )

    assert result.exit_code == 2, result.output
    assert "envs" in result.output, result.output
    assert calls == []


@pytest.mark.parametrize(
    "flag", ["--reconfigure-camera", "--reconfigure-microphone", "--reconfigure-speaker"]
)
def test_edge_install_rejects_environment_with_the_reconfigure_flags(monkeypatch, flag):
    """Those paths return before anything is attached, so -e would be a silent no-op."""
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
            normalize_environment_reference=_real_normalize_environment_reference,
        ),
    )

    result = CliRunner().invoke(
        _edge_module.install_edge, ["-e", "acme/envs/production-floor", flag]
    )

    assert result.exit_code == 2, result.output
    assert "does not apply" in result.output, result.output
    assert calls == []


def _stub_core(monkeypatch, calls):
    """Replace ``cyberwave_cli.core`` with a recording stub for CLI-level tests."""
    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
            normalize_environment_reference=_real_normalize_environment_reference,
        ),
    )


def test_edge_install_forwards_token_flag(monkeypatch):
    """``--token`` reaches ``setup_edge_core`` verbatim (CYB-3213)."""
    calls: list[dict[str, object]] = []
    _stub_core(monkeypatch, calls)

    result = CliRunner().invoke(_edge_module.install_edge, ["--token", "cw_secret", "--yes"])

    assert result.exit_code == 0, result.output
    assert calls[0]["token"] == "cw_secret"


def test_edge_install_forwards_token_and_environment_together(monkeypatch):
    """The scripted-provisioning combination this flag exists for."""
    calls: list[dict[str, object]] = []
    _stub_core(monkeypatch, calls)

    result = CliRunner().invoke(
        _edge_module.install_edge,
        ["--token", "cw_secret", "-e", "acme/envs/production-floor", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["token"] == "cw_secret"
    assert calls[0]["environment"] == "acme/envs/production-floor"


def test_edge_install_defaults_token_to_none(monkeypatch):
    """No ``--token`` must stay ``None`` so stored credentials still win."""
    calls: list[dict[str, object]] = []
    _stub_core(monkeypatch, calls)

    result = CliRunner().invoke(_edge_module.install_edge, ["--yes"])

    assert result.exit_code == 0, result.output
    assert calls[0]["token"] is None


def test_edge_install_rejects_empty_token_instead_of_falling_back(monkeypatch):
    """``--token "$UNSET_VAR"`` must not degrade into "use stored credentials"."""
    calls: list[dict[str, object]] = []
    _stub_core(monkeypatch, calls)

    result = CliRunner().invoke(_edge_module.install_edge, ["--token", "", "--yes"])

    assert result.exit_code == 2, result.output
    assert "empty value" in result.output, result.output
    assert calls == []


def test_edge_install_rejects_whitespace_only_token(monkeypatch):
    calls: list[dict[str, object]] = []
    _stub_core(monkeypatch, calls)

    result = CliRunner().invoke(_edge_module.install_edge, ["--token", "   ", "--yes"])

    assert result.exit_code == 2, result.output
    assert calls == []


def test_edge_install_strips_whitespace_around_token(monkeypatch):
    """A token pasted with a trailing newline must still authenticate."""
    calls: list[dict[str, object]] = []
    _stub_core(monkeypatch, calls)

    result = CliRunner().invoke(_edge_module.install_edge, ["--token", "  cw_secret\n", "--yes"])

    assert result.exit_code == 0, result.output
    assert calls[0]["token"] == "cw_secret"


@pytest.mark.parametrize(
    "flag", ["--reconfigure-camera", "--reconfigure-microphone", "--reconfigure-speaker"]
)
def test_edge_install_rejects_token_with_the_reconfigure_flags(monkeypatch, flag):
    """Those paths never contact the API, so --token would be a silent no-op."""
    calls: list[dict[str, object]] = []
    _stub_core(monkeypatch, calls)

    result = CliRunner().invoke(_edge_module.install_edge, ["--token", "cw_secret", flag])

    assert result.exit_code == 2, result.output
    assert "does not apply" in result.output, result.output
    assert calls == []


def test_edge_install_has_no_short_form_for_token():
    """``-t`` must stay unclaimed: ``-e`` already means two things across commands."""
    result = CliRunner().invoke(_edge_module.install_edge, ["-t", "cw_secret", "--yes"])

    assert result.exit_code == 2, result.output
    assert "No such option: -t" in result.output, result.output
