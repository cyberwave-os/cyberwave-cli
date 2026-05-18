"""Tests for the existing-configuration detection and cleanup during edge install."""

import json
from pathlib import Path

from tests._core_module_loader import load_core_module


def test_detect_existing_edge_configuration_returns_none_when_empty(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    env_label, twin_files = core._detect_existing_edge_configuration()

    assert env_label is None
    assert twin_files == []


def test_detect_existing_edge_configuration_finds_twin_json(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    twin_file = tmp_path / "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    twin_file.write_text(json.dumps({"uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}))

    env_file = tmp_path / "environment.json"
    env_file.write_text(json.dumps({"name": "My Env", "uuid": "env-uuid-123"}))
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", env_file)

    env_label, twin_files = core._detect_existing_edge_configuration()

    assert env_label == "My Env"
    assert len(twin_files) == 1
    assert twin_files[0].name == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"


def test_detect_existing_edge_configuration_falls_back_to_uuid(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    twin_file = tmp_path / "twin-uuid-1.json"
    twin_file.write_text("{}")

    env_file = tmp_path / "environment.json"
    env_file.write_text(json.dumps({"uuid": "env-uuid-456"}))
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", env_file)

    env_label, _ = core._detect_existing_edge_configuration()

    assert env_label == "env-uuid-456"


def test_detect_existing_edge_configuration_ignores_non_twin_files(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)

    for name in ("credentials.json", "environment.json", "cameras.json",
                  "camera_streams.json", "fingerprint.json"):
        (tmp_path / name).write_text("{}")

    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")

    env_label, twin_files = core._detect_existing_edge_configuration()

    assert env_label is None
    assert twin_files == []


def test_cleanup_existing_edge_configuration_removes_twin_and_env_files(
    monkeypatch, tmp_path
):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "FINGERPRINT_FILE", tmp_path / "fingerprint.json")

    twin_file = tmp_path / "twin-1.json"
    twin_file.write_text("{}")
    env_file = tmp_path / "environment.json"
    env_file.write_text(json.dumps({"name": "Old Env"}))
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", env_file)

    creds_file = tmp_path / "credentials.json"
    creds_file.write_text("{}")

    core._cleanup_existing_edge_configuration(
        twin_json_files=[twin_file],
        creds=None,
    )

    assert not twin_file.exists()
    assert not env_file.exists()
    assert creds_file.exists(), "credentials.json must be preserved"


def test_cleanup_existing_edge_configuration_calls_backend_cleanup(
    monkeypatch, tmp_path
):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")
    monkeypatch.setattr(core, "FINGERPRINT_FILE", tmp_path / "fingerprint.json")

    fp_file = tmp_path / "fingerprint.json"
    fp_file.write_text(json.dumps({"fingerprint": "fp-test-123"}))
    monkeypatch.setattr(core, "FINGERPRINT_FILE", fp_file)

    backend_calls: list[dict] = []

    def _fake_delete_edges(**kwargs):
        backend_calls.append(kwargs)
        return (1, 0)

    import types

    edge_mod = types.ModuleType("cyberwave_cli.commands.edge")
    edge_mod._delete_registered_edges_for_fingerprint = _fake_delete_edges
    monkeypatch.setitem(
        __import__("sys").modules, "cyberwave_cli.commands.edge", edge_mod
    )

    fake_creds = types.SimpleNamespace(
        token="tok-abc",
        cyberwave_base_url="https://api.test",
        workspace_uuid="ws-uuid-1",
    )

    core._cleanup_existing_edge_configuration(
        twin_json_files=[],
        creds=fake_creds,
    )

    assert len(backend_calls) == 1
    assert backend_calls[0]["fingerprint"] == "fp-test-123"
    assert backend_calls[0]["token"] == "tok-abc"


def test_configure_edge_environment_prompts_when_existing_config(
    monkeypatch, tmp_path
):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")
    monkeypatch.setattr(core, "FINGERPRINT_FILE", tmp_path / "fingerprint.json")

    twin_file = tmp_path / "twin-uuid-123.json"
    twin_file.write_text(json.dumps({"uuid": "twin-uuid-123"}))
    env_file = tmp_path / "environment.json"
    env_file.write_text(json.dumps({"name": "Production Floor", "uuid": "env-1"}))

    confirm_prompts: list[str] = []

    def _fake_confirm_ask(prompt, **kwargs):
        confirm_prompts.append(prompt)
        return False

    monkeypatch.setattr(core.Confirm, "ask", staticmethod(_fake_confirm_ask))

    fake_creds = type("Creds", (), {
        "token": "tok-abc",
        "cyberwave_base_url": "https://api.test",
        "workspace_uuid": "ws-1",
    })()
    monkeypatch.setattr(core, "load_credentials", lambda: fake_creds)

    workspace_selected = []

    class _FakeWorkspace:
        uuid = "ws-uuid"
        name = "Test Workspace"

    def _fake_get_sdk_client(*a, **kw):
        class _FakeClient:
            class workspaces:
                @staticmethod
                def list():
                    return [_FakeWorkspace()]
            class environments:
                @staticmethod
                def list(workspace_uuid):
                    return []
            class twins:
                @staticmethod
                def list(environment_uuid=None):
                    return []
        return _FakeClient()

    monkeypatch.setattr(core, "_get_sdk_client", _fake_get_sdk_client)
    monkeypatch.setattr(
        core, "_resolve_workspace_from_credentials", lambda *a, **kw: _FakeWorkspace()
    )
    monkeypatch.setattr(
        core, "_select_or_create_environment",
        lambda *a, **kw: type("E", (), {"uuid": "env-new", "name": "New Env"})()
    )
    monkeypatch.setattr(
        core, "_select_connected_twins", lambda *a, **kw: []
    )
    monkeypatch.setattr(
        core, "_detach_edge_fingerprint_from_other_twins",
        lambda *a, **kw: (0, 0)
    )
    monkeypatch.setattr(
        core, "_save_environment_file", lambda **kw: None
    )

    result = core.configure_edge_environment(skip_confirm=False)

    assert result is True
    assert len(confirm_prompts) >= 1
    assert "Production Floor" in confirm_prompts[0]


def test_configure_edge_environment_skips_prompt_with_skip_confirm(
    monkeypatch, tmp_path
):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")
    monkeypatch.setattr(core, "FINGERPRINT_FILE", tmp_path / "fingerprint.json")

    twin_file = tmp_path / "twin-uuid-123.json"
    twin_file.write_text(json.dumps({"uuid": "twin-uuid-123"}))
    env_file = tmp_path / "environment.json"
    env_file.write_text(json.dumps({"name": "Env1", "uuid": "env-1"}))

    confirm_prompts: list[str] = []

    def _fake_confirm_ask(prompt, **kwargs):
        confirm_prompts.append(prompt)
        return True

    monkeypatch.setattr(core.Confirm, "ask", staticmethod(_fake_confirm_ask))

    fake_creds = type("Creds", (), {
        "token": "tok-abc",
        "cyberwave_base_url": "https://api.test",
        "workspace_uuid": "ws-1",
    })()
    monkeypatch.setattr(core, "load_credentials", lambda: fake_creds)

    class _FakeWorkspace:
        uuid = "ws-uuid"
        name = "Test WS"

    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: type("C", (), {
        "workspaces": type("W", (), {"list": staticmethod(lambda: [_FakeWorkspace()])})()
    })())
    monkeypatch.setattr(
        core, "_resolve_workspace_from_credentials", lambda *a, **kw: _FakeWorkspace()
    )
    monkeypatch.setattr(
        core, "_select_or_create_environment",
        lambda *a, **kw: type("E", (), {"uuid": "env-new", "name": "New"})()
    )
    monkeypatch.setattr(core, "_select_connected_twins", lambda *a, **kw: [])
    monkeypatch.setattr(
        core, "_detach_edge_fingerprint_from_other_twins", lambda *a, **kw: (0, 0)
    )
    monkeypatch.setattr(core, "_save_environment_file", lambda **kw: None)

    result = core.configure_edge_environment(skip_confirm=True)

    assert result is True
    assert not any("already connected" in p for p in confirm_prompts)
