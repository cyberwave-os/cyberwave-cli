from __future__ import annotations

import importlib
import json
import sys
from types import ModuleType, SimpleNamespace
from uuid import UUID

import click
import pytest

from cyberwave_cli.credentials import Credentials

workflow_module = importlib.import_module("cyberwave_cli.commands.workflow")


def _node(
    *,
    node_type: str,
    trigger_type: str | None = None,
    twin_uuid: str | None = None,
    is_disabled: bool = False,
) -> SimpleNamespace:
    parameters = {"twin_uuid": twin_uuid} if twin_uuid is not None else {}
    return SimpleNamespace(
        node_type=node_type,
        trigger_type=trigger_type,
        parameters=parameters,
        is_disabled=is_disabled,
    )


def test_format_workflow_uuid_for_table_returns_full_uuid() -> None:
    workflow_uuid = "2a84297d-94d5-420d-bb2f-c905e64aea28"

    assert workflow_module._format_workflow_uuid_for_table(workflow_uuid) == workflow_uuid


def test_extract_twin_uuids_uses_all_enabled_twin_references() -> None:
    twin_a = "11111111-1111-1111-1111-111111111111"
    twin_b = "22222222-2222-2222-2222-222222222222"
    disabled = "99999999-9999-9999-9999-999999999999"

    assert workflow_module._extract_twin_uuids(
        [
            _node(
                node_type="trigger",
                trigger_type="camera_frame",
                twin_uuid=twin_a,
            ),
            _node(
                node_type="send_alert",
                twin_uuid=twin_b,
            ),
            _node(
                node_type="twin_control",
                twin_uuid=disabled,
                is_disabled=True,
            ),
            _node(
                node_type="conditional",
            ),
            _node(
                node_type="trigger",
                trigger_type="camera_frame",
                twin_uuid=twin_a,
            ),
        ]
    ) == [twin_a, twin_b]


def test_list_workflows_json_includes_edge_metadata_and_referenced_twins(
    monkeypatch,
) -> None:
    workflow = SimpleNamespace(
        uuid="wf-1",
        name="frame",
        is_active=True,
        description="",
        run_on_edge=True,
        environment_uuid=UUID("5069b42d-3e7d-4f55-a27b-880ab441720f"),
        execution_target="simulation",
    )
    node = _node(
        node_type="send_alert",
        twin_uuid="11111111-1111-1111-1111-111111111111",
    )
    printed: list[str] = []

    class Api:
        def src_app_api_workflows_list_workflows(self) -> list[SimpleNamespace]:
            return [workflow]

        def src_app_api_workflows_list_workflow_nodes(self, uuid: str) -> list[object]:
            assert uuid == "wf-1"
            return [node]

    client = SimpleNamespace(api=Api())

    monkeypatch.setattr(workflow_module, "get_sdk_client", lambda api_url=None: client)
    monkeypatch.setattr(workflow_module.console, "print", printed.append)

    workflow_module.list_workflows.callback(as_json=True, base_url=None)

    assert json.loads(printed[-1]) == [
        {
            "uuid": "wf-1",
            "name": "frame",
            "is_active": True,
            "run_on_edge": True,
            "environment_uuid": "5069b42d-3e7d-4f55-a27b-880ab441720f",
            "execution_target": "simulation",
            "description": "",
            "twin_uuids": ["11111111-1111-1111-1111-111111111111"],
        }
    ]


def test_list_workflows_table_uses_edge_cloud_target_labels(monkeypatch) -> None:
    workflows = [
        SimpleNamespace(
            uuid="2a84297d-94d5-420d-bb2f-c905e64aea28",
            name="frame",
            is_active=True,
            description="",
            run_on_edge=True,
            execution_target="simulation",
            environment_uuid=None,
        ),
        SimpleNamespace(
            uuid="c9ee956d-94d5-420d-bb2f-c905e64aea28",
            name="frame general",
            is_active=False,
            description="",
            run_on_edge=False,
            execution_target="physical",
            environment_uuid=None,
        ),
    ]
    captured: dict[str, object] = {"columns": [], "rows": []}

    class FakeTable:
        def __init__(self, *args, **kwargs):
            captured["title"] = kwargs.get("title")

        def add_column(self, header: str, *args, **kwargs) -> None:
            captured["columns"].append(header)

        def add_row(self, *cells: str) -> None:
            captured["rows"].append(cells)

    client = SimpleNamespace(
        api=SimpleNamespace(
            src_app_api_workflows_list_workflows=lambda: workflows,
            src_app_api_workflows_list_workflow_nodes=lambda uuid: [],
        )
    )
    printed: list[object] = []

    monkeypatch.setattr(workflow_module, "get_sdk_client", lambda api_url=None: client)
    monkeypatch.setattr(workflow_module, "Table", FakeTable)
    monkeypatch.setattr(workflow_module.console, "print", printed.append)

    workflow_module.list_workflows.callback(as_json=False, base_url=None)

    assert captured["columns"] == [
        "Name",
        "UUID",
        "Status",
        "Target",
        "Affect",
        "Environment UUID",
        "Twin(s)",
        "Description",
    ]
    assert captured["rows"][0][3] == "edge"
    assert captured["rows"][0][4] == "simulation"
    assert captured["rows"][1][3] == "cloud"
    assert captured["rows"][1][4] == "physical"


def test_pick_workflow_options_include_run_on_edge(monkeypatch) -> None:
    workflows = [
        SimpleNamespace(
            uuid="2a84297d-94d5-420d-bb2f-c905e64aea28",
            name="frame",
            is_active=True,
            run_on_edge=True,
        ),
        SimpleNamespace(
            uuid="c9ee956d-94d5-420d-bb2f-c905e64aea28",
            name="frame general",
            is_active=False,
            run_on_edge=False,
        ),
    ]
    captured: dict[str, object] = {}

    def fake_select(title: str, options: list[str]) -> int:
        captured["title"] = title
        captured["options"] = options
        return 1

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core._select_with_arrows = fake_select
    client = SimpleNamespace(
        api=SimpleNamespace(src_app_api_workflows_list_workflows=lambda: workflows)
    )
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)

    selected = workflow_module._pick_workflow(client, "Select a workflow to sync")

    assert selected == "c9ee956d-94d5-420d-bb2f-c905e64aea28"
    assert captured["title"] == "Select a workflow to sync"
    assert captured["options"] == [
        "frame [\033[32mActive\033[0m] [edge] (2a84297d...)",
        "frame general [\033[2mInactive\033[0m] [cloud] (c9ee956d...)",
    ]


def test_api_get_json_uses_stored_credential_base_url(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def read(self) -> bytes:
            return b'{"ok": true}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        return Response()

    monkeypatch.delenv("CYBERWAVE_BASE_URL", raising=False)
    monkeypatch.setattr(
        workflow_module,
        "load_credentials",
        lambda: Credentials(
            token="token-123",
            cyberwave_base_url="http://localhost:8000",
        ),
    )
    monkeypatch.setattr(workflow_module, "urlopen", fake_urlopen)

    assert workflow_module._api_get_json("/api/v1/workflows") == {"ok": True}
    assert captured == {
        "url": "http://localhost:8000/api/v1/workflows",
        "authorization": "Bearer token-123",
        "timeout": 15,
    }


def test_sync_workflow_uses_workflow_node_twins_for_preflight(monkeypatch) -> None:
    target_twin = "11111111-1111-1111-1111-111111111111"
    workflow = SimpleNamespace(uuid="wf-1", name="Navigation", run_on_edge=True)
    captured: dict[str, object] = {}

    class Api:
        def src_app_api_workflows_get_workflow(self, uuid: str) -> SimpleNamespace:
            assert uuid == "wf-1"
            return workflow

        def src_app_api_workflows_list_workflow_nodes(self, uuid: str) -> list[object]:
            assert uuid == "wf-1"
            return [_node(node_type="twin_control", twin_uuid=target_twin)]

    client = SimpleNamespace(api=Api())

    monkeypatch.setattr(workflow_module, "get_sdk_client", lambda api_url=None: client)
    monkeypatch.setattr(
        workflow_module,
        "_load_local_edge_twin_uuids",
        lambda: (_ for _ in ()).throw(
            AssertionError("sync should not read local edge twins")
        ),
    )
    monkeypatch.setattr(workflow_module, "_print_workflow_metadata", lambda w: None)

    def fake_preflight(w, twin_uuids, base_url):
        captured["workflow"] = w
        captured["twin_uuids"] = twin_uuids
        captured["base_url"] = base_url
        return list(twin_uuids), []

    monkeypatch.setattr(workflow_module, "_preflight_sync", fake_preflight)

    workflow_module.sync_workflow.callback(
        uuid="wf-1",
        force=False,
        dry_run=True,
        edge_active=False,
        base_url=None,
    )

    assert captured == {
        "workflow": workflow,
        "twin_uuids": [target_twin],
        "base_url": None,
    }


def test_sync_workflow_requires_workflow_node_twins(monkeypatch) -> None:
    workflow = SimpleNamespace(uuid="wf-1", name="Navigation", run_on_edge=True)
    client = SimpleNamespace(
        api=SimpleNamespace(
            src_app_api_workflows_get_workflow=lambda uuid: workflow,
            src_app_api_workflows_list_workflow_nodes=lambda uuid: [],
        )
    )

    monkeypatch.setattr(workflow_module, "get_sdk_client", lambda api_url=None: client)
    monkeypatch.setattr(
        workflow_module,
        "_load_local_edge_twin_uuids",
        lambda: (_ for _ in ()).throw(
            AssertionError("sync should not read local edge twins")
        ),
    )
    monkeypatch.setattr(workflow_module, "_print_workflow_metadata", lambda w: None)

    with pytest.raises(click.Abort):
        workflow_module.sync_workflow.callback(
            uuid="wf-1",
            force=False,
            dry_run=True,
            edge_active=False,
            base_url=None,
        )


def test_sync_workflow_rejects_cloud_target_before_loading_workflow_nodes(
    monkeypatch,
) -> None:
    workflow = SimpleNamespace(uuid="wf-1", name="Cloud workflow", run_on_edge=False)
    client = SimpleNamespace(
        api=SimpleNamespace(
            src_app_api_workflows_get_workflow=lambda uuid: workflow,
            src_app_api_workflows_list_workflow_nodes=lambda uuid: (_ for _ in ()).throw(
                AssertionError("cloud workflow should fail before loading workflow nodes")
            ),
        )
    )
    printed: list[str] = []

    monkeypatch.setattr(workflow_module, "get_sdk_client", lambda api_url=None: client)
    monkeypatch.setattr(workflow_module, "_print_workflow_metadata", lambda w: None)
    monkeypatch.setattr(
        workflow_module.console,
        "print",
        lambda message: printed.append(str(message)),
    )

    with pytest.raises(click.Abort):
        workflow_module.sync_workflow.callback(
            uuid="wf-1",
            force=False,
            dry_run=True,
            edge_active=False,
            base_url=None,
        )

    assert any("targets cloud execution" in message for message in printed)


def test_sync_workflow_edge_active_filters_picker_to_active_edge_workflows(
    monkeypatch,
) -> None:
    """``--edge-active`` should only show active + run_on_edge workflows."""
    target_twin = "11111111-1111-1111-1111-111111111111"
    edge_active_wf = SimpleNamespace(
        uuid="wf-edge-active",
        name="Edge active",
        is_active=True,
        run_on_edge=True,
    )
    inactive_edge_wf = SimpleNamespace(
        uuid="wf-edge-inactive",
        name="Edge inactive",
        is_active=False,
        run_on_edge=True,
    )
    active_cloud_wf = SimpleNamespace(
        uuid="wf-cloud-active",
        name="Cloud active",
        is_active=True,
        run_on_edge=False,
    )
    workflows = [edge_active_wf, inactive_edge_wf, active_cloud_wf]

    def get_workflow(uuid: str) -> SimpleNamespace:
        assert uuid == "wf-edge-active"
        return edge_active_wf

    def list_nodes(uuid: str) -> list[object]:
        assert uuid == "wf-edge-active"
        return [_node(node_type="twin_control", twin_uuid=target_twin)]

    client = SimpleNamespace(
        api=SimpleNamespace(
            src_app_api_workflows_list_workflows=lambda: workflows,
            src_app_api_workflows_get_workflow=get_workflow,
            src_app_api_workflows_list_workflow_nodes=list_nodes,
        )
    )
    captured: dict[str, object] = {}

    def fake_select(title: str, options: list[str]) -> int:
        captured["title"] = title
        captured["options"] = options
        return 0

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core._select_with_arrows = fake_select
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setattr(workflow_module, "get_sdk_client", lambda api_url=None: client)
    monkeypatch.setattr(workflow_module, "_print_workflow_metadata", lambda w: None)
    monkeypatch.setattr(
        workflow_module,
        "_preflight_sync",
        lambda w, twin_uuids, base_url: (list(twin_uuids), []),
    )

    workflow_module.sync_workflow.callback(
        uuid=None,
        force=False,
        dry_run=True,
        edge_active=True,
        base_url=None,
    )

    assert captured["title"] == "Select an active edge workflow to sync"
    assert captured["options"] == [
        "Edge active [\033[32mActive\033[0m] [edge] (wf-edge-...)",
    ]


def test_sync_workflow_edge_active_without_matches_aborts(monkeypatch) -> None:
    """Abort with a helpful hint when no workflow is both active and edge-target."""
    workflows = [
        SimpleNamespace(
            uuid="wf-cloud",
            name="Cloud",
            is_active=True,
            run_on_edge=False,
        ),
        SimpleNamespace(
            uuid="wf-inactive",
            name="Inactive",
            is_active=False,
            run_on_edge=True,
        ),
    ]
    client = SimpleNamespace(
        api=SimpleNamespace(src_app_api_workflows_list_workflows=lambda: workflows)
    )
    printed: list[str] = []

    def fake_select(title: str, options: list[str]) -> int:
        raise AssertionError("selector should not be invoked when filter is empty")

    fake_core = ModuleType("cyberwave_cli.core")
    fake_core._select_with_arrows = fake_select
    monkeypatch.setitem(sys.modules, "cyberwave_cli.core", fake_core)
    monkeypatch.setattr(workflow_module, "get_sdk_client", lambda api_url=None: client)
    monkeypatch.setattr(
        workflow_module.console,
        "print",
        lambda message: printed.append(str(message)),
    )

    with pytest.raises(click.Abort):
        workflow_module.sync_workflow.callback(
            uuid=None,
            force=False,
            dry_run=True,
            edge_active=True,
            base_url=None,
        )

    assert any("No active edge workflows found" in message for message in printed)


def test_sync_workflow_edge_active_with_explicit_uuid_is_rejected(monkeypatch) -> None:
    """``--edge-active`` + explicit UUID is a usage error."""
    client = SimpleNamespace(api=SimpleNamespace())
    printed: list[str] = []

    monkeypatch.setattr(workflow_module, "get_sdk_client", lambda api_url=None: client)
    monkeypatch.setattr(
        workflow_module.console,
        "print",
        lambda message: printed.append(str(message)),
    )

    with pytest.raises(click.Abort):
        workflow_module.sync_workflow.callback(
            uuid="wf-1",
            force=False,
            dry_run=True,
            edge_active=True,
            base_url=None,
        )

    assert any(
        "--edge-active" in message and "explicit workflow UUID" in message
        for message in printed
    )
