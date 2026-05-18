import importlib
import sys
from types import ModuleType, SimpleNamespace

edge_module = importlib.import_module("cyberwave_cli.commands.edge")


def test_sync_workflows_without_twin_syncs_local_environment_twins(monkeypatch):
    import cyberwave_cli.config as config
    import cyberwave_cli.utils as utils

    called: dict[str, object] = {}
    printed: list[str] = []
    successes: list[str] = []
    errors: list[tuple[str, str | None]] = []

    startup = ModuleType("cyberwave_edge_core.startup")
    startup.DEFAULT_API_URL = "https://api.example.test"
    startup.load_token = lambda: "token-123"
    startup.load_selected_twin_uuids = lambda: ["twin-a", "twin-b"]
    startup.get_runtime_env_var = lambda key, default=None: "http://localhost:8000"

    def _sync_workers_for_twins(*, token, twin_uuids, base_url):
        called["sync"] = {
            "token": token,
            "twin_uuids": twin_uuids,
            "base_url": base_url,
        }
        return {
            "twin-a": {"written": 1, "removed": 0, "unchanged": 0, "errors": 0},
            "twin-b": {"written": 0, "removed": 1, "unchanged": 2, "errors": 0},
        }

    startup._sync_workers_for_twins = _sync_workers_for_twins
    edge_core = ModuleType("cyberwave_edge_core")
    edge_core.startup = startup

    monkeypatch.setitem(sys.modules, "cyberwave_edge_core", edge_core)
    monkeypatch.setitem(sys.modules, "cyberwave_edge_core.startup", startup)
    monkeypatch.setattr(config, "ensure_edge_core_importable", lambda: None)
    monkeypatch.setattr(
        utils,
        "get_sdk_client",
        lambda: (_ for _ in ()).throw(AssertionError("MQTT path should not run")),
    )
    monkeypatch.setattr(utils, "print_success", lambda message: successes.append(message))
    monkeypatch.setattr(
        utils,
        "print_error",
        lambda message, hint=None: errors.append((message, hint)),
    )
    monkeypatch.setattr(
        edge_module.console,
        "print",
        lambda message="", *args, **kwargs: printed.append(str(message)),
    )

    edge_module.sync_workflows.callback(twin_uuid=None)

    assert called["sync"] == {
        "token": "token-123",
        "twin_uuids": ["twin-a", "twin-b"],
        "base_url": "http://localhost:8000",
    }
    assert printed == [
        "[cyan]Syncing workflow workers locally for 2 twin(s)...[/cyan]",
        "  [bold]twin-a[/bold] written=1 removed=0 unchanged=0 errors=0",
        "  [bold]twin-b[/bold] written=0 removed=1 unchanged=2 errors=0",
    ]
    assert not errors
    assert successes == [
        "Workflow sync complete (written=1, removed=1, unchanged=2)."
    ]


def test_sync_workflows_with_twin_keeps_mqtt_trigger(monkeypatch):
    import cyberwave_cli.config as config
    import cyberwave_cli.utils as utils

    published: list[tuple[str, dict[str, str]]] = []
    successes: list[str] = []

    client = SimpleNamespace(
        mqtt=SimpleNamespace(
            publish_command_message=lambda twin_uuid, payload: published.append(
                (twin_uuid, payload)
            )
        )
    )

    monkeypatch.setattr(
        config,
        "ensure_edge_core_importable",
        lambda: (_ for _ in ()).throw(
            AssertionError("local edge-core path should not run")
        ),
    )
    monkeypatch.setattr(utils, "get_sdk_client", lambda: client)
    monkeypatch.setattr(utils, "print_success", lambda message: successes.append(message))

    edge_module.sync_workflows.callback(twin_uuid="twin-123")

    assert published == [("twin-123", {"command": "sync_workflows"})]
    assert successes == ["Command sent. Check edge logs for results."]
