from types import SimpleNamespace

from tests._core_module_loader import load_core_module as _load_core_module


class _FakeProjectsManager:
    def __init__(self, projects):
        self._projects = projects

    def list(self):
        return list(self._projects)


class _FakeEnvironmentsManager:
    def __init__(self, all_envs, envs_by_project):
        self._all_envs = all_envs
        self._envs_by_project = envs_by_project
        self.calls: list[str | None] = []

    def list(self, project_id=None):
        self.calls.append(project_id)
        if project_id is None:
            return list(self._all_envs)
        return list(self._envs_by_project.get(project_id, []))


class _FakeTwinsManager:
    def __init__(self, twins, *, fail_on_update=None, allowed_environment_id=None):
        self._twins = twins
        self._fail_on_update = set(fail_on_update or [])
        self._allowed_environment_id = allowed_environment_id
        self.updated: list[tuple[str, dict]] = []
        self.list_calls: list[str | None] = []

    def list(self, environment_id=None):
        self.list_calls.append(environment_id)
        if (
            self._allowed_environment_id is not None
            and environment_id != self._allowed_environment_id
        ):
            return []
        return list(self._twins)

    def update(self, twin_uuid, metadata):
        if twin_uuid in self._fail_on_update:
            raise RuntimeError("simulated update failure")
        self.updated.append((str(twin_uuid), metadata))


def test_workspace_environments_includes_standalone_and_project_scoped(monkeypatch):
    core = _load_core_module(monkeypatch)

    workspace_uuid = "ws-1"
    project_ws_1 = SimpleNamespace(uuid="project-1", workspace_uuid=workspace_uuid)
    project_ws_2 = SimpleNamespace(uuid="project-2", workspace_uuid="ws-2")

    env_in_project = SimpleNamespace(uuid="env-project", workspace_uuid=workspace_uuid)
    standalone_env = SimpleNamespace(
        uuid="env-standalone",
        workspace_uuid=workspace_uuid,
        project_uuid=None,
    )
    other_workspace_env = SimpleNamespace(uuid="env-other", workspace_uuid="ws-2")

    environments = _FakeEnvironmentsManager(
        all_envs=[standalone_env, other_workspace_env],
        envs_by_project={
            "project-1": [env_in_project],
            "project-2": [other_workspace_env],
        },
    )
    client = SimpleNamespace(
        projects=_FakeProjectsManager([project_ws_1, project_ws_2]),
        environments=environments,
    )

    result = core._workspace_environments(client, workspace_uuid)
    uuids = [env.uuid for env in result]

    assert set(uuids) == {"env-project", "env-standalone"}
    assert "project-1" in environments.calls
    assert None in environments.calls


def test_workspace_environments_uses_settings_workspace_uuid_for_standalone(monkeypatch):
    core = _load_core_module(monkeypatch)

    workspace_uuid = "ws-1"
    standalone_env = SimpleNamespace(
        uuid="env-standalone",
        workspace_uuid=None,
        settings={"_workspace_uuid": workspace_uuid},
    )
    unrelated_env = SimpleNamespace(
        uuid="env-other",
        workspace_uuid=None,
        settings={"_workspace_uuid": "ws-2"},
    )

    environments = _FakeEnvironmentsManager(
        all_envs=[standalone_env, unrelated_env],
        envs_by_project={},
    )
    client = SimpleNamespace(
        projects=_FakeProjectsManager([]),
        environments=environments,
    )

    result = core._workspace_environments(client, workspace_uuid)

    assert [env.uuid for env in result] == ["env-standalone"]


def test_workspace_environments_deduplicates_between_project_and_global_lists(monkeypatch):
    core = _load_core_module(monkeypatch)

    workspace_uuid = "ws-1"
    project_ws_1 = SimpleNamespace(uuid="project-1", workspace_uuid=workspace_uuid)
    duplicated = SimpleNamespace(uuid="env-dup", workspace_uuid=workspace_uuid)
    standalone = SimpleNamespace(uuid="env-standalone", workspace_uuid=workspace_uuid)

    environments = _FakeEnvironmentsManager(
        all_envs=[duplicated, standalone],
        envs_by_project={"project-1": [duplicated]},
    )
    client = SimpleNamespace(
        projects=_FakeProjectsManager([project_ws_1]),
        environments=environments,
    )

    result = core._workspace_environments(client, workspace_uuid)

    assert [env.uuid for env in result] == ["env-dup", "env-standalone"]


def test_detach_edge_fingerprint_sends_explicit_null_to_delete_key(monkeypatch):
    """Regression: backend treats missing keys as "unchanged" and only
    explicit ``None`` values as deletions, so the detach call must send
    ``{"edge_fingerprint": None}`` rather than a stripped copy of the
    existing metadata. Otherwise stale fingerprints from previous installs
    survive forever and pin unwanted driver containers to this edge.
    """
    core = _load_core_module(monkeypatch)

    environment_uuid = "env-1"
    fingerprint = "fp-123"
    selected_twin = SimpleNamespace(uuid="twin-selected", metadata={"edge_fingerprint": fingerprint})
    stale_twin = SimpleNamespace(
        uuid="twin-stale",
        metadata={"edge_fingerprint": fingerprint, "label": "old-binding"},
    )
    unrelated_twin = SimpleNamespace(uuid="twin-other", metadata={"edge_fingerprint": "other-fp"})
    malformed_twin = SimpleNamespace(uuid="twin-malformed", metadata="not-a-dict")

    twins = _FakeTwinsManager(
        [selected_twin, stale_twin, unrelated_twin, malformed_twin],
        allowed_environment_id=environment_uuid,
    )
    client = SimpleNamespace(twins=twins)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client,
        environment_uuid=environment_uuid,
        keep_twin_uuids=["twin-selected"],
        edge_fingerprint=fingerprint,
    )

    assert detached == 1
    assert failed == 0
    assert twins.list_calls == [environment_uuid]
    # The payload must send an explicit null so the backend deletes the key.
    # Sending the remaining metadata without ``edge_fingerprint`` would merge
    # on top of the stored copy, leaving the stale fingerprint intact.
    assert twins.updated == [("twin-stale", {"edge_fingerprint": None})]


def test_detach_edge_fingerprint_from_other_twins_counts_update_failures(monkeypatch):
    core = _load_core_module(monkeypatch)

    environment_uuid = "env-1"
    fingerprint = "fp-123"
    stale_twin = SimpleNamespace(uuid="twin-stale", metadata={"edge_fingerprint": fingerprint})
    twins = _FakeTwinsManager(
        [stale_twin],
        fail_on_update={"twin-stale"},
        allowed_environment_id=environment_uuid,
    )
    client = SimpleNamespace(twins=twins)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client,
        environment_uuid=environment_uuid,
        keep_twin_uuids=[],
        edge_fingerprint=fingerprint,
    )

    assert detached == 0
    assert failed == 1


def test_attach_edge_fingerprint_sends_only_the_key(monkeypatch):
    """Attach must send only the single key it wants to write; the backend's
    merge semantics preserve the rest of the metadata. Sending a full dict
    copied off an SDK wrapper risks overwriting with an empty ``{}`` when
    the wrapper doesn't expose ``.metadata`` (as ``LocomoteCameraTwin`` did
    not), silently wiping ``drivers``, ``attach_to``, etc.
    """
    core = _load_core_module(monkeypatch)

    twins = _FakeTwinsManager(
        twins=[],
        fail_on_update={"twin-broken"},
    )
    client = SimpleNamespace(twins=twins)

    updated, failed = core._attach_edge_fingerprint_to_twins(
        client,
        twin_uuids=["twin-a", "twin-broken", "twin-c"],
        edge_fingerprint="fp-123",
    )

    assert updated == 2
    assert failed == 1
    assert twins.updated == [
        ("twin-a", {"edge_fingerprint": "fp-123"}),
        ("twin-c", {"edge_fingerprint": "fp-123"}),
    ]


class _FakeWorkspacesManager:
    def __init__(self, workspaces):
        self._workspaces = workspaces

    def list(self):
        return list(self._workspaces)


def test_resolve_workspace_from_credentials_matches_uuid(monkeypatch):
    core = _load_core_module(monkeypatch)
    ws = SimpleNamespace(uuid="4144c982-2f9e-47ba-9504-4e0e4ec2ad11", name="test")
    other = SimpleNamespace(uuid="00000000-0000-0000-0000-000000000001", name="other")
    client = SimpleNamespace(workspaces=_FakeWorkspacesManager([other, ws]))

    found = core._resolve_workspace_from_credentials(
        client, "4144c982-2f9e-47ba-9504-4e0e4ec2ad11"
    )
    assert found is ws


def test_resolve_workspace_from_credentials_returns_none_when_missing(monkeypatch):
    core = _load_core_module(monkeypatch)
    client = SimpleNamespace(
        workspaces=_FakeWorkspacesManager(
            [SimpleNamespace(uuid="11111111-1111-1111-1111-111111111111", name="only")]
        )
    )

    assert core._resolve_workspace_from_credentials(client, "22222222-2222-2222-2222-222222222222") is None


def test_resolve_workspace_from_credentials_empty_uuid(monkeypatch):
    core = _load_core_module(monkeypatch)
    client = SimpleNamespace(workspaces=_FakeWorkspacesManager([]))

    assert core._resolve_workspace_from_credentials(client, "") is None
    assert core._resolve_workspace_from_credentials(client, "   ") is None
