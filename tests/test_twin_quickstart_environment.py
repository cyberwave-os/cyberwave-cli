"""CLI twin create must reuse SDK quickstart environment resolution."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_twin_commands(monkeypatch):
    package_root = Path(__file__).resolve().parents[1] / "cyberwave_cli"

    utils_module = sys.modules.get("cyberwave_cli.utils")
    if utils_module is None:
        utils_module = type(sys)("cyberwave_cli.utils")
        utils_module.console = MagicMock()
        utils_module.print_error = lambda *args, **kwargs: None
        utils_module.print_success = lambda *args, **kwargs: None
        utils_module.print_warning = lambda *args, **kwargs: None
        utils_module.truncate_uuid = lambda value: value
        utils_module.write_edge_env = lambda **_kwargs: None
        utils_module.get_sdk_client = lambda: None
        monkeypatch.setitem(sys.modules, "cyberwave_cli.utils", utils_module)

    fingerprint_module = type(sys)("cyberwave.fingerprint")
    fingerprint_module.get_device_info = lambda: {"hostname": "edge-host"}
    monkeypatch.setitem(sys.modules, "cyberwave.fingerprint", fingerprint_module)

    sys.modules.pop("cyberwave_cli.commands.twin", None)
    spec = importlib.util.spec_from_file_location(
        "cyberwave_cli.commands.twin",
        package_root / "commands" / "twin.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["cyberwave_cli.commands.twin"] = module
    spec.loader.exec_module(module)
    return module


def test_resolve_quickstart_environment_reuses_existing(monkeypatch):
    twin_commands = _load_twin_commands(monkeypatch)
    client = MagicMock()
    client.get_or_create_quickstart_environment.return_value = (
        "existing-env-uuid",
        False,
    )

    env_id = twin_commands._resolve_quickstart_environment(client)

    assert env_id == "existing-env-uuid"
    client.get_or_create_quickstart_environment.assert_called_once()
    client.environments.create.assert_not_called()


def test_resolve_quickstart_environment_creates_when_missing(monkeypatch):
    twin_commands = _load_twin_commands(monkeypatch)
    client = MagicMock()
    client.get_or_create_quickstart_environment.return_value = ("new-env-uuid", True)

    env_id = twin_commands._resolve_quickstart_environment(client)

    assert env_id == "new-env-uuid"
    client.get_or_create_quickstart_environment.assert_called_once()
    client.environments.create.assert_not_called()


def test_find_or_create_twin_uses_quickstart_when_environment_unset(monkeypatch):
    twin_commands = _load_twin_commands(monkeypatch)
    client = MagicMock()
    client.get_or_create_quickstart_environment.return_value = (
        "existing-env-uuid",
        False,
    )
    client.twins.list.return_value = []
    created_twin = SimpleNamespace(uuid="twin-uuid", name="My Twin")
    client.twins.create.return_value = created_twin

    asset = {"uuid": "asset-uuid", "name": "Robot"}

    twin = twin_commands._find_or_create_twin(
        client,
        asset=asset,
        fingerprint="fp-1",
        environment_uuid=None,
        twin_name="My Twin",
        yes=True,
    )

    assert twin is created_twin
    client.get_or_create_quickstart_environment.assert_called_once()
    client.environments.create.assert_not_called()
    client.twins.create.assert_called_once_with(
        name="My Twin",
        environment_id="existing-env-uuid",
        asset_id="asset-uuid",
    )


def test_find_or_create_twin_uses_picker_when_pick_environment_set(monkeypatch):
    twin_commands = _load_twin_commands(monkeypatch)
    client = MagicMock()
    client.twins.list.return_value = []
    created_twin = SimpleNamespace(uuid="twin-uuid", name="My Twin")
    client.twins.create.return_value = created_twin

    asset = {"uuid": "asset-uuid", "name": "Robot"}

    with patch.object(
        twin_commands,
        "_select_environment",
        return_value="picked-env-uuid",
    ) as picker:
        twin = twin_commands._find_or_create_twin(
            client,
            asset=asset,
            fingerprint=None,
            environment_uuid=None,
            twin_name="My Twin",
            yes=True,
            pick_environment=True,
        )

    assert twin is created_twin
    picker.assert_called_once()
    client.get_or_create_quickstart_environment.assert_not_called()
    client.twins.create.assert_called_once_with(
        name="My Twin",
        environment_id="picked-env-uuid",
        asset_id="asset-uuid",
    )


def test_find_or_create_twin_skips_quickstart_when_environment_provided(monkeypatch):
    twin_commands = _load_twin_commands(monkeypatch)
    client = MagicMock()
    client.twins.list.return_value = []
    created_twin = SimpleNamespace(uuid="twin-uuid", name="My Twin")
    client.twins.create.return_value = created_twin

    asset = {"uuid": "asset-uuid", "name": "Robot"}

    twin = twin_commands._find_or_create_twin(
        client,
        asset=asset,
        fingerprint="fp-1",
        environment_uuid="explicit-env-uuid",
        twin_name="My Twin",
        yes=True,
    )

    assert twin is created_twin
    client.get_or_create_quickstart_environment.assert_not_called()
    client.twins.create.assert_called_once_with(
        name="My Twin",
        environment_id="explicit-env-uuid",
        asset_id="asset-uuid",
    )
