"""Tests for the top-level ``cyberwave pair`` alias of ``cyberwave edge install``."""

from __future__ import annotations

import importlib
import sys
import types

from click.testing import CliRunner

import cyberwave_cli.main as main_module

_pair_module = importlib.import_module("cyberwave_cli.commands.pair")
_edge_module = importlib.import_module("cyberwave_cli.commands.edge")


def test_pair_is_registered_as_top_level_lazy_command() -> None:
    assert "pair" in main_module._LAZY_COMMANDS
    assert main_module._LAZY_COMMANDS["pair"] == (".commands.pair", "pair")


def test_pair_shares_callback_and_params_with_edge_install() -> None:
    assert _pair_module.pair.callback is _edge_module.install_edge.callback
    pair_params = {p.name for p in _pair_module.pair.params}
    install_params = {p.name for p in _edge_module.install_edge.params}
    assert pair_params == install_params


def test_pair_command_forwards_flags_to_setup_edge_core(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    monkeypatch.setitem(
        sys.modules,
        "cyberwave_cli.core",
        types.SimpleNamespace(
            setup_edge_core=lambda **kwargs: calls.append(kwargs) or True,
        ),
    )

    result = CliRunner().invoke(
        main_module.cli,
        ["pair", "--channel", "dev", "--version", "0.3.1.dev5", "--yes"],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "skip_confirm": True,
            "channel": "dev",
            "version": "0.3.1.dev5",
            "force_reinstall": False,
            "pull_worker_image": True,
        }
    ]


def test_pair_command_appears_in_root_help() -> None:
    result = CliRunner().invoke(main_module.cli, ["--help"])

    assert result.exit_code == 0
    assert "pair" in result.output


def test_pair_is_eagerly_reexported_from_commands_package() -> None:
    """``cyberwave_cli.commands`` statically re-exports every command so that
    PyInstaller's import-graph analyser bundles them all into the standalone
    binary — the ``_LAZY_COMMANDS`` dict in ``main`` uses ``importlib`` with
    relative string paths, which PyInstaller cannot follow statically. Any
    lazy command that's not also listed here will ``ModuleNotFoundError`` at
    runtime in the binary build (e.g. when ``cyberwave --help`` iterates the
    Click group).
    """
    import cyberwave_cli.commands as commands_pkg

    assert "pair" in commands_pkg.__all__
    assert getattr(commands_pkg, "pair", None) is _pair_module.pair
