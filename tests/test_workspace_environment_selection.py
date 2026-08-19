import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from tests._core_module_loader import load_core_module as _load_core_module


def _install_fake_auth_client(
    monkeypatch,
    *,
    workspace_uuid=None,
    workspace_name="",
    rejects_token=False,
    fails_with_status=None,
):
    """Point the stubbed ``cyberwave_cli.auth`` at a fake token-context client.

    ``rejects_token`` simulates a token the endpoint refuses (e.g. a Knox
    session token, which has no bound workspace) — the backend answers 403, and
    that is how ``_api_token_workspace`` decides it has no workspace context.

    ``fails_with_status`` simulates the other failures ``AuthClient`` funnels
    into the same exception type: pass an int for an HTTP status (500, 404) or
    ``"transport"`` for a connection error, which carries no status at all.
    """
    auth_module = sys.modules["cyberwave_cli.auth"]

    class _FakeAuthenticationError(Exception):
        def __init__(self, message, details=None, status_code=None):
            super().__init__(message)
            self.details = details
            self.status_code = status_code

    class _FakeAuthClient:
        def __init__(self, base_url=None):
            self.base_url = base_url

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get_api_token_context(self, token):
            if rejects_token:
                raise _FakeAuthenticationError("not an API token", status_code=403)
            if fails_with_status is not None:
                if fails_with_status == "transport":
                    raise _FakeAuthenticationError("Connection error: refused")
                raise _FakeAuthenticationError(
                    f"HTTP error: {fails_with_status}", status_code=fails_with_status
                )
            return SimpleNamespace(
                email="edge@example.test",
                workspace_uuid=workspace_uuid,
                workspace_name=workspace_name,
            )

    monkeypatch.setattr(auth_module, "AuthClient", _FakeAuthClient)
    monkeypatch.setattr(auth_module, "AuthenticationError", _FakeAuthenticationError)


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
        self.get_calls: list[str] = []

    def list(self, project_id=None):
        self.calls.append(project_id)
        if project_id is None:
            return list(self._all_envs)
        return list(self._envs_by_project.get(project_id, []))

    def get(self, environment_id):
        """Resolve by UUID or full slug, mirroring the SDK's own ``get``."""
        self.get_calls.append(environment_id)
        for env in self._all_envs:
            if environment_id in {
                str(getattr(env, "uuid", "")),
                str(getattr(env, "slug", "") or ""),
            }:
                return env
        raise RuntimeError("404 Environment not found")


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


class _FakeEdgesManager:
    """``client.edges`` for the sweep.

    *no_binding_twins* are ones the unpair endpoint answers
    ``{"success": False}`` for — it had nothing to remove.
    """

    def __init__(
        self,
        edges,
        bound=None,
        fail_list=False,
        fail_get_twins=False,
        fail_unpair=(),
        no_binding_twins=(),
    ):
        self._edges = edges
        self._bound = bound or {}
        self._fail_list = fail_list
        self._fail_get_twins = fail_get_twins
        self._fail_unpair = set(fail_unpair)
        self._no_binding = set(no_binding_twins)
        self.get_twins_calls: list[str] = []
        self.unpair_calls: list[tuple[str, str]] = []

    def list(self):
        if self._fail_list:
            raise RuntimeError("connection reset")
        return list(self._edges)

    def get_twins(self, edge_id):
        self.get_twins_calls.append(str(edge_id))
        if self._fail_get_twins:
            raise RuntimeError("500 Server Error")
        return list(self._bound.get(str(edge_id), []))

    def unpair_twin(self, edge_id, twin_id):
        self.unpair_calls.append((str(edge_id), str(twin_id)))
        if twin_id in self._fail_unpair:
            raise RuntimeError("403 Forbidden")
        return {"success": twin_id not in self._no_binding}


def _edge(uuid, fingerprint, workspace_uuid):
    return SimpleNamespace(uuid=uuid, fingerprint=fingerprint, workspace_uuid=workspace_uuid)


def _binding(twin_uuid, name="cam"):
    return {"twin_uuid": twin_uuid, "twin_name": name, "camera_config": {}}


def _client(edges, twins=None):
    return SimpleNamespace(edges=edges, twins=twins if twins is not None else _FakeTwinsManager([]))


def test_detach_edge_fingerprint_clears_bindings_in_other_environments(monkeypatch):
    """Regression: a twin bound by an earlier pairing into another environment
    was never swept, so it kept the fingerprint forever."""
    core = _load_core_module(monkeypatch)

    twins = _FakeTwinsManager([])
    edges = _FakeEdgesManager(
        [_edge("edge-1", "fp-123", "ws-1")],
        bound={"edge-1": [_binding("twin-selected"), _binding("twin-previous")]},
    )
    client = _client(edges, twins)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client,
        workspace_uuid="ws-1",
        keep_twin_uuids=["twin-selected"],
        edge_fingerprint="fp-123",
    )

    assert (detached, failed) == (1, 0)
    assert edges.unpair_calls == [("edge-1", "twin-previous")]
    assert twins.list_calls == [], "the sweep must not enumerate twins client-side"
    assert edges.get_twins_calls == ["edge-1"]


def test_detach_edge_fingerprint_ignores_an_edge_row_in_another_workspace(monkeypatch):
    """A host re-paired into a new workspace must not clear the old one."""
    core = _load_core_module(monkeypatch)

    twins = _FakeTwinsManager([])
    edges = _FakeEdgesManager(
        [_edge("edge-elsewhere", "fp-123", "ws-2")],
        bound={"edge-elsewhere": [_binding("twin-theirs")]},
    )
    client = _client(edges, twins)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client,
        workspace_uuid="ws-1",
        keep_twin_uuids=[],
        edge_fingerprint="fp-123",
    )

    assert (detached, failed) == (0, 0)
    assert edges.unpair_calls == []
    assert edges.get_twins_calls == [], "must not even ask for another workspace's bindings"


def test_detach_edge_fingerprint_is_a_noop_before_the_edge_exists(monkeypatch):
    """First install: no edge row, so nothing was ever bound."""
    core = _load_core_module(monkeypatch)

    twins = _FakeTwinsManager([])
    edges = _FakeEdgesManager([])
    client = _client(edges, twins)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client,
        workspace_uuid="ws-1",
        keep_twin_uuids=[],
        edge_fingerprint="fp-123",
    )

    assert (detached, failed) == (0, 0)
    assert edges.get_twins_calls == []


def test_detach_edge_fingerprint_unpairs_through_the_edge_endpoint(monkeypatch):
    """The unpair endpoint clears the legacy ``edge_configs`` shape too; a
    metadata merge on ``edge_fingerprint`` reaches only the canonical one."""
    core = _load_core_module(monkeypatch)

    twins = _FakeTwinsManager([])
    edges = _FakeEdgesManager(
        [_edge("edge-1", "fp-123", "ws-1")],
        bound={"edge-1": [_binding("twin-stale")]},
    )
    client = _client(edges, twins)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client,
        workspace_uuid="ws-1",
        keep_twin_uuids=[],
        edge_fingerprint="fp-123",
    )

    assert (detached, failed) == (1, 0)
    assert edges.unpair_calls == [("edge-1", "twin-stale")]
    assert twins.updated == [], "the sweep must not write twin metadata directly"


def test_detach_edge_fingerprint_does_not_count_what_it_did_not_remove(monkeypatch):
    """Regression: a twin the endpoint reports ``success: False`` for must not
    be counted, or the CLI claims to have fixed a binding that survived."""
    core = _load_core_module(monkeypatch)

    edges = _FakeEdgesManager(
        [_edge("edge-1", "fp-123", "ws-1")],
        bound={"edge-1": [_binding("twin-untouched")]},
        no_binding_twins=["twin-untouched"],
    )
    client = _client(edges)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client, workspace_uuid="ws-1", keep_twin_uuids=[], edge_fingerprint="fp-123"
    )

    assert (detached, failed) == (0, 0)
    assert edges.unpair_calls == [("edge-1", "twin-untouched")]


def test_detach_edge_fingerprint_skips_malformed_binding_rows(monkeypatch):
    """A row that is not a dict must be skipped, not raised on."""
    core = _load_core_module(monkeypatch)

    edges = _FakeEdgesManager(
        [_edge("edge-1", "fp-123", "ws-1")],
        bound={"edge-1": ["not-a-dict", None, _binding("twin-stale")]},
    )
    client = _client(edges)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client, workspace_uuid="ws-1", keep_twin_uuids=[], edge_fingerprint="fp-123"
    )

    assert (detached, failed) == (1, 0)
    assert edges.unpair_calls == [("edge-1", "twin-stale")]


def test_edge_uuid_for_fingerprint_requires_a_workspace(monkeypatch):
    """``get_twins`` on a workspace-less edge matches every workspace, so an
    unknown workspace must resolve to no edge rather than to a wide sweep."""
    core = _load_core_module(monkeypatch)

    edges = _FakeEdgesManager([_edge("edge-1", "fp-123", "")])

    assert core._edge_uuid_for_fingerprint(_client(edges), "fp-123", "") is None
    assert core._edge_uuid_for_fingerprint(_client(edges), "fp-123", "ws-1") is None


def test_detach_edge_fingerprint_reports_a_failed_edge_listing(monkeypatch):
    core = _load_core_module(monkeypatch)

    edges = _FakeEdgesManager([], fail_list=True)
    client = _client(edges)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client, workspace_uuid="ws-1", keep_twin_uuids=[], edge_fingerprint="fp-123"
    )

    assert (detached, failed) == (0, 1)


def test_detach_edge_fingerprint_reports_a_failed_bindings_lookup(monkeypatch):
    core = _load_core_module(monkeypatch)

    twins = _FakeTwinsManager([])
    edges = _FakeEdgesManager([_edge("edge-1", "fp-123", "ws-1")], fail_get_twins=True)
    client = _client(edges, twins)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client, workspace_uuid="ws-1", keep_twin_uuids=[], edge_fingerprint="fp-123"
    )

    assert (detached, failed) == (0, 1)
    assert edges.unpair_calls == []


def test_detach_edge_fingerprint_never_raises_into_the_install(monkeypatch):
    """``configure_edge_environment`` catches Exception and returns False."""
    core = _load_core_module(monkeypatch)

    class _ExplodingEdges:
        def list(self):
            raise RuntimeError("boom")

    client = _client(_ExplodingEdges())

    try:
        detached, failed = core._detach_edge_fingerprint_from_other_twins(
            client, workspace_uuid="ws-1", keep_twin_uuids=[], edge_fingerprint="fp-123"
        )
    except Exception as exc:  # noqa: BLE001 - the assertion is the point
        pytest.fail(f"the sweep must not propagate {type(exc).__name__}")

    assert (detached, failed) == (0, 1)


def test_detach_edge_fingerprint_from_other_twins_counts_unpair_failures(monkeypatch):
    core = _load_core_module(monkeypatch)

    edges = _FakeEdgesManager(
        [_edge("edge-1", "fp-123", "ws-1")],
        bound={"edge-1": [_binding("twin-stale")]},
        fail_unpair=["twin-stale"],
    )
    client = _client(edges)

    detached, failed = core._detach_edge_fingerprint_from_other_twins(
        client, workspace_uuid="ws-1", keep_twin_uuids=[], edge_fingerprint="fp-123"
    )

    assert (detached, failed) == (0, 1)


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


def test_select_workspace_prefers_the_api_tokens_own_workspace(monkeypatch):
    """Regression: ``workspaces.list()`` returns every workspace the user can
    reach, but an API token can only list environments inside the single
    workspace it was minted for. Picking ``workspaces[0]`` therefore selected a
    workspace whose environment listing is always empty, so the edge install
    reported "no environments found" even though the token worked fine.
    """
    core = _load_core_module(monkeypatch)
    monkeypatch.delenv("CYBERWAVE_WORKSPACE_SLUG", raising=False)
    _install_fake_auth_client(
        monkeypatch, workspace_uuid="ws-token", workspace_name="Token Workspace"
    )

    first = SimpleNamespace(uuid="ws-first", name="First Workspace", slug="first")
    bound = SimpleNamespace(uuid="ws-token", name="Token Workspace", slug="token")
    client = SimpleNamespace(workspaces=_FakeWorkspacesManager([first, bound]))

    selected = core._select_workspace(client, skip_confirm=True, token="cw_test")

    assert selected is bound


def test_select_workspace_rejects_slug_that_contradicts_the_token(monkeypatch):
    core = _load_core_module(monkeypatch)
    monkeypatch.setenv("CYBERWAVE_WORKSPACE_SLUG", "first")
    _install_fake_auth_client(
        monkeypatch, workspace_uuid="ws-token", workspace_name="Token Workspace"
    )

    first = SimpleNamespace(uuid="ws-first", name="First Workspace", slug="first")
    bound = SimpleNamespace(uuid="ws-token", name="Token Workspace", slug="token")
    client = SimpleNamespace(workspaces=_FakeWorkspacesManager([first, bound]))

    with pytest.raises(RuntimeError, match="bound to workspace 'Token Workspace'"):
        core._select_workspace(client, skip_confirm=True, token="cw_test")


def test_select_workspace_rejects_slug_conflict_when_bound_workspace_is_unlisted(monkeypatch):
    """The token context carries no slug, so the conflict check must compare
    UUIDs — a slug-to-slug comparison silently passed whenever the bound
    workspace was missing from the listing.
    """
    core = _load_core_module(monkeypatch)
    monkeypatch.setenv("CYBERWAVE_WORKSPACE_SLUG", "first")
    _install_fake_auth_client(
        monkeypatch, workspace_uuid="ws-token", workspace_name="Token Workspace"
    )

    first = SimpleNamespace(uuid="ws-first", name="First Workspace", slug="first")
    client = SimpleNamespace(workspaces=_FakeWorkspacesManager([first]))

    with pytest.raises(RuntimeError, match="CYBERWAVE_WORKSPACE_SLUG is 'first'"):
        core._select_workspace(client, skip_confirm=True, token="cw_test")


def test_select_workspace_honours_matching_slug_with_token(monkeypatch):
    core = _load_core_module(monkeypatch)
    monkeypatch.setenv("CYBERWAVE_WORKSPACE_SLUG", "token")
    _install_fake_auth_client(
        monkeypatch, workspace_uuid="ws-token", workspace_name="Token Workspace"
    )

    first = SimpleNamespace(uuid="ws-first", name="First Workspace", slug="first")
    bound = SimpleNamespace(uuid="ws-token", name="Token Workspace", slug="token")
    client = SimpleNamespace(workspaces=_FakeWorkspacesManager([first, bound]))

    assert core._select_workspace(client, skip_confirm=True, token="cw_test") is bound


def test_select_workspace_still_rejects_unknown_slug(monkeypatch):
    core = _load_core_module(monkeypatch)
    monkeypatch.setenv("CYBERWAVE_WORKSPACE_SLUG", "nope")
    _install_fake_auth_client(monkeypatch, rejects_token=True)

    first = SimpleNamespace(uuid="ws-first", name="First Workspace", slug="first")
    client = SimpleNamespace(workspaces=_FakeWorkspacesManager([first]))

    with pytest.raises(RuntimeError, match="was not found"):
        core._select_workspace(client, skip_confirm=True, token="knox-token")


def test_select_workspace_uses_token_context_when_workspace_is_not_listed(monkeypatch):
    core = _load_core_module(monkeypatch)
    monkeypatch.delenv("CYBERWAVE_WORKSPACE_SLUG", raising=False)
    _install_fake_auth_client(
        monkeypatch, workspace_uuid="ws-token", workspace_name="Token Workspace"
    )

    first = SimpleNamespace(uuid="ws-first", name="First Workspace", slug="first")
    client = SimpleNamespace(workspaces=_FakeWorkspacesManager([first]))

    selected = core._select_workspace(client, skip_confirm=True, token="cw_test")

    assert str(selected.uuid) == "ws-token"
    assert selected.name == "Token Workspace"


def test_select_workspace_falls_back_when_token_has_no_workspace_context(monkeypatch):
    """Knox session tokens have no bound workspace, so the previous
    first-workspace / slug behaviour must stay intact for them.
    """
    core = _load_core_module(monkeypatch)
    monkeypatch.delenv("CYBERWAVE_WORKSPACE_SLUG", raising=False)
    _install_fake_auth_client(monkeypatch, rejects_token=True)

    first = SimpleNamespace(uuid="ws-first", name="First Workspace", slug="first")
    second = SimpleNamespace(uuid="ws-second", name="Second Workspace", slug="second")
    client = SimpleNamespace(workspaces=_FakeWorkspacesManager([first, second]))

    selected = core._select_workspace(client, skip_confirm=True, token="knox-token")

    assert selected is first


def test_persist_corrected_workspace_rewrites_stored_workspace(monkeypatch):
    """``creds.workspace_uuid`` also filters edge-registration cleanup, so a
    mismatched value has to be repaired on disk and not just in memory.
    """
    core = _load_core_module(monkeypatch)

    @dataclass
    class _Creds:
        token: str
        email: str | None = None
        workspace_uuid: str | None = None
        workspace_name: str | None = None

    saved: list = []
    monkeypatch.setattr(core, "save_credentials", saved.append)

    creds = _Creds(token="cw_test", email="edge@example.test", workspace_uuid="ws-stale")
    workspace = SimpleNamespace(uuid="ws-token", name="Token Workspace")

    corrected = core._persist_corrected_workspace(creds, workspace)

    assert corrected.workspace_uuid == "ws-token"
    assert corrected.workspace_name == "Token Workspace"
    assert corrected.email == "edge@example.test"  # unrelated fields survive
    assert saved == [corrected]


def test_persist_corrected_workspace_keeps_going_when_the_write_fails(monkeypatch):
    core = _load_core_module(monkeypatch)

    @dataclass
    class _Creds:
        token: str
        workspace_uuid: str | None = None
        workspace_name: str | None = None

    def _boom(_creds):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(core, "save_credentials", _boom)
    monkeypatch.setattr(core, "console", SimpleNamespace(print=lambda *a, **k: None))

    creds = _Creds(token="cw_test", workspace_uuid="ws-stale")
    result = core._persist_corrected_workspace(creds, SimpleNamespace(uuid="ws-token", name="T"))

    # An unwritable credentials file must not abort the install.
    assert result is creds


def test_api_token_workspace_returns_none_without_workspace_uuid(monkeypatch):
    core = _load_core_module(monkeypatch)
    _install_fake_auth_client(monkeypatch, workspace_uuid="", workspace_name="")

    assert core._api_token_workspace("cw_test") is None


def test_workspace_environments_reports_when_every_env_belongs_elsewhere(monkeypatch):
    """An empty result must not look like "this account has no environments"
    when the API actually returned environments from other workspaces.
    """
    core = _load_core_module(monkeypatch)

    printed: list[str] = []
    monkeypatch.setattr(
        core, "console", SimpleNamespace(print=lambda *args, **kwargs: printed.append(str(args[0])))
    )

    other_env = SimpleNamespace(uuid="env-other", workspace_uuid="ws-2")
    client = SimpleNamespace(
        projects=_FakeProjectsManager([]),
        environments=_FakeEnvironmentsManager(all_envs=[other_env], envs_by_project={}),
    )

    assert core._workspace_environments(client, "ws-1") == []
    assert any("none belong to workspace ws-1" in line for line in printed)
    assert any("ws-2" in line for line in printed)


def test_workspace_environments_surfaces_listing_errors(monkeypatch):
    core = _load_core_module(monkeypatch)

    printed: list[str] = []
    monkeypatch.setattr(
        core, "console", SimpleNamespace(print=lambda *args, **kwargs: printed.append(str(args[0])))
    )

    class _FailingEnvironments:
        def list(self, project_id=None):
            raise RuntimeError("boom")

    client = SimpleNamespace(
        projects=_FakeProjectsManager([]),
        environments=_FailingEnvironments(),
    )

    # Reported to the operator *and* raised: printing alone still returned an
    # empty list, which the caller reads as "this workspace has no environments"
    # and answers by creating one.
    with pytest.raises(RuntimeError, match="Could not determine the environments"):
        core._workspace_environments(client, "ws-1")
    assert any("Could not list environments" in line for line in printed)


# ---- -e/--environment (CYB-3213) ---------------------------------------------

_ENV_UUID = "11111111-2222-3333-4444-555555555555"


def _environment_client(*envs, token_alive=True):
    """A client whose ``workspaces.list()`` stands in for the token liveness probe."""

    def _list_workspaces():
        if not token_alive:
            raise RuntimeError("401 Unauthorized: Invalid token")
        return [SimpleNamespace(uuid="ws-1", name="Test Workspace")]

    return SimpleNamespace(
        projects=_FakeProjectsManager([]),
        environments=_FakeEnvironmentsManager(all_envs=list(envs), envs_by_project={}),
        workspaces=SimpleNamespace(list=_list_workspaces),
    )


class _Status:
    """Stand-in for ``console.status``, which is used as a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _silence_console(monkeypatch, core):
    printed: list[str] = []
    monkeypatch.setattr(
        core,
        "console",
        SimpleNamespace(
            print=lambda *args, **kwargs: printed.append(str(args[0])),
            status=lambda *args, **kwargs: _Status(),
        ),
    )
    return printed


def test_resolve_requested_environment_accepts_uuid(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Production Floor", workspace_uuid="ws-1")
    client = _environment_client(env)

    assert core._resolve_requested_environment(client, "ws-1", _ENV_UUID) is env
    assert client.environments.get_calls == [_ENV_UUID]


def test_resolve_requested_environment_accepts_full_slug(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    env = SimpleNamespace(
        uuid=_ENV_UUID,
        name="Production Floor",
        slug="acme/envs/production-floor",
        workspace_uuid="ws-1",
    )
    client = _environment_client(env)

    resolved = core._resolve_requested_environment(client, "ws-1", "acme/envs/production-floor")

    assert resolved is env


def test_resolve_requested_environment_tolerates_surrounding_whitespace(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Prod", workspace_uuid="ws-1")
    client = _environment_client(env)

    assert core._resolve_requested_environment(client, "ws-1", f"  {_ENV_UUID}\n") is env


@pytest.mark.parametrize(
    "reference",
    [
        "production-floor",
        "acme/production-floor",
        "acme/envs/",
        # Same three-part shape, wrong entity — a URL pasted from the twin page.
        "acme/twins/arm-station-1",
        "acme/workflows/pick-and-place",
    ],
)
def test_resolve_requested_environment_rejects_references_that_are_not_uuid_or_full_slug(
    monkeypatch, reference
):
    """A bare name is the likely mistake, so the error must name the format."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Prod", workspace_uuid="ws-1")
    client = _environment_client(env)

    with pytest.raises(ValueError, match="envs"):
        core._resolve_requested_environment(client, "ws-1", reference)

    assert client.environments.get_calls == [], "must not call the API for a malformed reference"


def test_resolve_requested_environment_rejects_empty_reference(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    client = _environment_client()

    with pytest.raises(ValueError, match="empty"):
        core._resolve_requested_environment(client, "ws-1", "   ")


def test_resolve_requested_environment_aborts_when_not_found(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    client = _environment_client()

    with pytest.raises(RuntimeError, match="could not be loaded"):
        core._resolve_requested_environment(client, "ws-1", _ENV_UUID)


def test_resolve_requested_environment_aborts_when_the_api_returns_none(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    client = SimpleNamespace(
        projects=_FakeProjectsManager([]),
        environments=SimpleNamespace(get=lambda environment_id: None),
    )

    with pytest.raises(RuntimeError, match="was not found"):
        core._resolve_requested_environment(client, "ws-1", _ENV_UUID)


def test_resolve_requested_environment_rejects_another_workspaces_environment(monkeypatch):
    """A share link makes an out-of-workspace environment readable; attaching to
    it would split the edge from its twins."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Someone Else's Floor", workspace_uuid="ws-2")
    client = _environment_client(env)

    with pytest.raises(RuntimeError, match="belongs to workspace ws-2, not ws-1"):
        core._resolve_requested_environment(client, "ws-1", _ENV_UUID)


def test_resolve_requested_environment_allows_unresolvable_workspace(monkeypatch):
    """Standalone environments may have no workspace; only a known-different one
    aborts, or valid input gets rejected."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Standalone", workspace_uuid=None, settings={})
    client = _environment_client(env)

    assert core._resolve_requested_environment(client, "ws-1", _ENV_UUID) is env


def test_resolve_requested_environment_never_creates(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    created: list[dict] = []

    class _CreatingEnvironments(_FakeEnvironmentsManager):
        def create(self, **kwargs):
            created.append(kwargs)
            raise AssertionError("--environment must never create an environment")

    client = SimpleNamespace(
        projects=_FakeProjectsManager([]),
        environments=_CreatingEnvironments(all_envs=[], envs_by_project={}),
    )

    with pytest.raises(RuntimeError):
        core._resolve_requested_environment(client, "ws-1", _ENV_UUID)

    assert created == []


def test_configure_edge_environment_treats_empty_environment_as_an_error(monkeypatch):
    """An empty value must not fall through to selection."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    monkeypatch.setattr(
        core,
        "load_credentials",
        lambda: SimpleNamespace(
            token="tok", cyberwave_base_url="https://api.test", workspace_uuid="ws-1"
        ),
    )
    monkeypatch.setattr(core, "_api_token_workspace", lambda *a, **kw: None)
    monkeypatch.setattr(
        core,
        "_resolve_workspace_from_credentials",
        lambda *a, **kw: SimpleNamespace(uuid="ws-1", name="WS"),
    )
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: SimpleNamespace())
    monkeypatch.setattr(
        core,
        "_select_or_create_environment",
        lambda *a, **kw: (_ for _ in ()).throw(
            AssertionError("an empty --environment must not fall back to selection")
        ),
    )
    monkeypatch.setattr(core, "_save_environment_file", lambda **kw: None)

    assert core.configure_edge_environment(skip_confirm=True, environment="") is False


def _fake_creds(**overrides):
    base = {"token": "tok", "cyberwave_base_url": "https://api.test", "workspace_uuid": "ws-1"}
    base.update(overrides)
    return SimpleNamespace(**base)


def _fake_token_workspace(monkeypatch, core, uuid="ws-1"):
    monkeypatch.setattr(
        core,
        "_api_token_workspace",
        lambda *a, **kw: None if uuid is None else SimpleNamespace(uuid=uuid, name="WS"),
    )


def test_preflight_environment_reference_accepts_a_resolvable_environment(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Prod", workspace_uuid="ws-1")
    monkeypatch.setattr(core, "load_credentials", _fake_creds)
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: _environment_client(env))
    _fake_token_workspace(monkeypatch, core)

    assert core._preflight_environment_reference(_ENV_UUID) is True


def test_preflight_environment_reference_proceeds_when_the_stored_token_is_stale(monkeypatch):
    """A 401 says nothing about the environment, and ``_ensure_credentials`` inside
    ``setup_service`` recovers via CYBERWAVE_API_KEY or an interactive login.
    Aborting here would block an install that would have succeeded."""
    core = _load_core_module(monkeypatch)
    printed = _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Prod", workspace_uuid="ws-1")
    client = _environment_client(env, token_alive=False)
    monkeypatch.setattr(core, "load_credentials", _fake_creds)
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: client)

    assert core._preflight_environment_reference(_ENV_UUID) is True
    assert client.environments.get_calls == [], "a dead token must not be used to look up"
    assert any("Skipping the environment pre-check" in line for line in printed)


def test_preflight_environment_reference_rejects_another_workspaces_environment(monkeypatch):
    """A public or share-linked environment is readable across workspaces, so the
    successful lookup has to be checked against the token's own workspace."""
    core = _load_core_module(monkeypatch)
    printed = _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Someone Else's Floor", workspace_uuid="ws-2")
    monkeypatch.setattr(core, "load_credentials", _fake_creds)
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: _environment_client(env))
    _fake_token_workspace(monkeypatch, core, uuid="ws-1")

    assert core._preflight_environment_reference(_ENV_UUID) is False
    assert any("belongs to workspace ws-2, not ws-1" in line for line in printed)


def test_preflight_environment_reference_falls_back_to_the_stored_workspace(monkeypatch):
    """Without this the abort lands in ``configure_edge_environment`` instead —
    after apt-get, Docker, and the macOS USB/IP build."""
    core = _load_core_module(monkeypatch)
    printed = _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Prod", workspace_uuid="ws-2")
    monkeypatch.setattr(core, "load_credentials", _fake_creds)
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: _environment_client(env))
    _fake_token_workspace(monkeypatch, core, uuid=None)

    assert core._preflight_environment_reference(_ENV_UUID) is False
    assert any("belongs to workspace ws-2, not ws-1" in line for line in printed)


def test_preflight_environment_reference_ignores_a_stale_stored_workspace(monkeypatch):
    """Only a definite answer aborts: a stale stored workspace means
    ``configure_edge_environment`` may legitimately land somewhere else."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Prod", workspace_uuid="ws-2")
    monkeypatch.setattr(core, "load_credentials", lambda: _fake_creds(workspace_uuid="ws-gone"))
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: _environment_client(env))
    _fake_token_workspace(monkeypatch, core, uuid=None)

    assert core._preflight_environment_reference(_ENV_UUID) is True


def test_preflight_environment_reference_ignores_the_stored_workspace_with_a_token(monkeypatch):
    """The stored workspace belongs to the outgoing credential, so it says
    nothing about where an explicit ``--token`` can attach."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Prod", workspace_uuid="ws-2")
    monkeypatch.setattr(core, "load_credentials", _fake_creds)
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: _environment_client(env))
    _fake_token_workspace(monkeypatch, core, uuid=None)

    assert core._preflight_environment_reference(_ENV_UUID, token="cw_explicit") is True


def test_preflight_environment_reference_normalizes_its_own_input(monkeypatch):
    """``setup_edge_core`` is public, so the preflight cannot rely on the click
    callback having validated the reference."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    env = SimpleNamespace(uuid=_ENV_UUID, name="Prod", workspace_uuid="ws-1")
    client = _environment_client(env)
    monkeypatch.setattr(core, "load_credentials", _fake_creds)
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: client)
    _fake_token_workspace(monkeypatch, core)

    assert core._preflight_environment_reference(f"  {_ENV_UUID}\n") is True
    assert client.environments.get_calls == [_ENV_UUID]

    with pytest.raises(ValueError, match="envs"):
        core._preflight_environment_reference("production-floor")


def test_preflight_environment_reference_rejects_an_unknown_environment(monkeypatch):
    core = _load_core_module(monkeypatch)
    printed = _silence_console(monkeypatch, core)

    monkeypatch.setattr(core, "load_credentials", _fake_creds)
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: _environment_client())
    _fake_token_workspace(monkeypatch, core)

    assert core._preflight_environment_reference(_ENV_UUID) is False
    assert any("Nothing was installed" in line for line in printed)


def test_preflight_environment_reference_skips_without_credentials(monkeypatch):
    """First-ever pair logs in inside setup_service, so this cannot be the last
    line of defence."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    monkeypatch.setattr(core, "load_credentials", lambda: None)
    monkeypatch.setattr(
        core,
        "_get_sdk_client",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not build a client")),
    )

    assert core._preflight_environment_reference(_ENV_UUID) is True


def test_setup_edge_core_aborts_before_installing_when_environment_is_unknown(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    monkeypatch.setattr(core, "_preflight_environment_reference", lambda ref, **kw: False)
    monkeypatch.setattr(
        core,
        "setup_service",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not reach setup_service")),
    )

    assert core.setup_edge_core(skip_confirm=True, environment=_ENV_UUID) is False


def test_setup_edge_core_skips_the_preflight_without_an_environment(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    monkeypatch.setattr(
        core,
        "_preflight_environment_reference",
        lambda ref, **kw: (_ for _ in ()).throw(AssertionError("no --environment to preflight")),
    )
    monkeypatch.setattr(core, "setup_service", lambda *a, **kw: True)

    assert core.setup_edge_core(skip_confirm=True) is True


# --- a failed token-context lookup must not silently revert to guessing -------


@pytest.mark.parametrize(
    "failure",
    [500, 502, 404, "transport"],
    ids=["server-error", "bad-gateway", "endpoint-missing", "connection-refused"],
)
def test_api_token_workspace_reports_failures_that_are_not_a_rejected_token(monkeypatch, failure):
    """``AuthClient`` funnels 5xx, 404 and connection errors into the same
    ``AuthenticationError`` it uses for a token with no workspace context. Only
    401/403 mean the latter; treating the rest as "no context" reverts to the
    ``workspaces[0]`` guess that this whole path exists to eliminate, and does it
    without printing anything.
    """
    core = _load_core_module(monkeypatch)
    _install_fake_auth_client(monkeypatch, fails_with_status=failure)

    printed: list[str] = []
    monkeypatch.setattr(
        core, "console", SimpleNamespace(print=lambda *a, **k: printed.append(str(a[0])))
    )

    assert core._api_token_workspace("cw_test") is None
    assert any("Could not resolve the workspace bound to this token" in p for p in printed)


def test_api_token_workspace_stays_quiet_for_a_token_without_workspace_context(monkeypatch):
    """A Knox session token is answered with 403 and is not a failure — the
    caller is expected to fall back, so warning about it would be noise on a
    perfectly normal path.
    """
    core = _load_core_module(monkeypatch)
    _install_fake_auth_client(monkeypatch, rejects_token=True)

    printed: list[str] = []
    monkeypatch.setattr(
        core, "console", SimpleNamespace(print=lambda *a, **k: printed.append(str(a[0])))
    )

    assert core._api_token_workspace("knox-token") is None
    assert printed == []


# --- a contradicting slug must abort, not fall through to interactive login ---


def test_ensure_credentials_aborts_on_slug_conflict_instead_of_prompting(monkeypatch):
    """The conflict used to be caught by the broad ``except Exception`` around
    the ``CYBERWAVE_API_KEY`` branch, which reported it as "invalid or
    incomplete" and then dropped into the email/password prompt — wrong
    diagnosis, and it hangs (or EOFs) an unattended install.
    """
    core = _load_core_module(monkeypatch)
    monkeypatch.setenv("CYBERWAVE_API_KEY", "cw_test")
    monkeypatch.setenv("CYBERWAVE_WORKSPACE_SLUG", "first")
    _install_fake_auth_client(
        monkeypatch, workspace_uuid="ws-token", workspace_name="Token Workspace"
    )

    printed: list[str] = []

    class _Status:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        core,
        "console",
        SimpleNamespace(
            print=lambda *a, **k: printed.append(str(a[0])),
            status=lambda *a, **k: _Status(),
        ),
    )

    first = SimpleNamespace(uuid="ws-first", name="First Workspace", slug="first")
    bound = SimpleNamespace(uuid="ws-token", name="Token Workspace", slug="token")
    monkeypatch.setattr(
        core,
        "_get_sdk_client",
        lambda *a, **kw: SimpleNamespace(workspaces=_FakeWorkspacesManager([first, bound])),
    )
    monkeypatch.setattr(core, "load_credentials", lambda: None)
    monkeypatch.setattr(core, "save_credentials", lambda creds: None)
    monkeypatch.setattr(core, "collect_runtime_env_overrides", lambda **_kwargs: {})

    def _never_prompt(*_args, **_kwargs):
        raise AssertionError("must not prompt for credentials on a workspace conflict")

    monkeypatch.setattr(core, "Prompt", SimpleNamespace(ask=_never_prompt))

    assert core._ensure_credentials(skip_confirm=True) is False
    assert any("bound to workspace 'Token Workspace'" in p for p in printed)
    assert not any("invalid or incomplete" in p for p in printed)


def test_workspace_conflict_error_is_a_runtime_error(monkeypatch):
    """Callers that only know about ``RuntimeError`` keep working."""
    core = _load_core_module(monkeypatch)

    assert issubclass(core.WorkspaceConflictError, RuntimeError)


# --- a failed listing must not be read as "this workspace is empty" ----------


def test_workspace_environments_raises_when_the_listing_failed(monkeypatch):
    """Returning ``[]`` here is what makes ``_select_or_create_environment``
    create a stray ``Edge Environment`` on a transient API error — the exact
    outcome the workspace fix exists to prevent, reached by another route.
    """
    core = _load_core_module(monkeypatch)
    monkeypatch.setattr(core, "console", SimpleNamespace(print=lambda *a, **k: None))

    class _FailingEnvironments:
        def list(self, project_id=None):
            raise RuntimeError("503 Service Unavailable")

    client = SimpleNamespace(
        projects=_FakeProjectsManager([]),
        environments=_FailingEnvironments(),
    )

    with pytest.raises(RuntimeError, match="Could not determine the environments"):
        core._workspace_environments(client, "ws-1")


def test_workspace_environments_keeps_project_scoped_results_when_the_global_list_fails(
    monkeypatch,
):
    """A partial failure that the project-scoped pass can still satisfy must not
    abort: only a genuinely unknown result is worth stopping for.
    """
    core = _load_core_module(monkeypatch)
    monkeypatch.setattr(core, "console", SimpleNamespace(print=lambda *a, **k: None))

    project = SimpleNamespace(uuid="proj-1", workspace_uuid="ws-1")
    env = SimpleNamespace(uuid="env-1", workspace_uuid="ws-1")

    class _PartlyFailingEnvironments:
        def list(self, project_id=None):
            if project_id is None:
                raise RuntimeError("503 Service Unavailable")
            return [env]

    client = SimpleNamespace(
        projects=_FakeProjectsManager([project]),
        environments=_PartlyFailingEnvironments(),
    )

    assert core._workspace_environments(client, "ws-1") == [env]


def test_workspace_environments_still_returns_empty_for_a_genuinely_empty_workspace(monkeypatch):
    """No error means an empty result is trustworthy, and creating an
    environment is the correct next step.
    """
    core = _load_core_module(monkeypatch)
    monkeypatch.setattr(core, "console", SimpleNamespace(print=lambda *a, **k: None))

    client = SimpleNamespace(
        projects=_FakeProjectsManager([]),
        environments=_FakeEnvironmentsManager(all_envs=[], envs_by_project={}),
    )

    assert core._workspace_environments(client, "ws-1") == []


# ---- --token (CYB-3213) ------------------------------------------------------


def _token_auth_env(monkeypatch, core, *, workspaces=("ws-1",), clients=None):
    """Wire up the collaborators ``_authenticate_with_api_token`` needs.

    Returns the list that ``save_credentials`` writes into. *clients*, when
    given, records each ``(token, base_url)`` the SDK client was built with.
    """
    saved: list[object] = []

    listed = [
        SimpleNamespace(uuid=uuid, name=f"Workspace {uuid}", slug=uuid) for uuid in workspaces
    ]

    def _client(token, base_url=None):
        if clients is not None:
            clients.append((token, base_url))
        return SimpleNamespace(workspaces=SimpleNamespace(list=lambda: listed))

    monkeypatch.setattr(core, "_get_sdk_client", _client)
    monkeypatch.setattr(core, "save_credentials", saved.append)
    monkeypatch.setattr(core, "Credentials", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(core, "collect_runtime_env_overrides", lambda **_kwargs: {})
    monkeypatch.setattr(
        core.Prompt,
        "ask",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not prompt")),
    )
    return saved


def test_ensure_credentials_token_overrides_valid_stored_credentials(monkeypatch):
    """The whole point of the flag: re-pairing a device must not silently keep
    the old workspace just because the old token still validates."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)
    _install_fake_auth_client(monkeypatch, workspace_uuid="ws-1", workspace_name="Workspace ws-1")
    clients: list[tuple[str, str | None]] = []
    saved = _token_auth_env(monkeypatch, core, clients=clients)

    monkeypatch.setattr(
        core, "load_credentials", lambda: _fake_creds(token="cw_old", email="old@example.test")
    )

    assert core._ensure_credentials(skip_confirm=True, token="cw_new") is True
    assert [c.token for c in saved] == ["cw_new"]
    assert saved[0].workspace_uuid == "ws-1"
    assert [t for t, _ in clients] == ["cw_new"], "the stored token must never be used"


def test_ensure_credentials_token_keeps_the_devices_backend(monkeypatch):
    """``get_api_url()`` only reads CYBERWAVE_BASE_URL, so a dev/staging device
    keeps its backend solely in the stored credentials. --token swaps the
    credential, not the backend — probing production would reject a good token,
    and persisting ``None`` would strand the device on the next run."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)
    _install_fake_auth_client(monkeypatch, workspace_uuid="ws-1", workspace_name="Workspace ws-1")
    clients: list[tuple[str, str | None]] = []
    saved = _token_auth_env(monkeypatch, core, clients=clients)

    monkeypatch.setattr(
        core,
        "load_credentials",
        lambda: _fake_creds(token="cw_old", cyberwave_base_url="https://dev.api.test"),
    )
    monkeypatch.delenv("CYBERWAVE_BASE_URL", raising=False)

    assert core._ensure_credentials(skip_confirm=True, token="cw_new") is True
    assert clients == [("cw_new", "https://dev.api.test")]
    assert saved[0].cyberwave_base_url == "https://dev.api.test"


def test_ensure_credentials_token_lets_the_env_override_the_stored_backend(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)
    _install_fake_auth_client(monkeypatch, workspace_uuid="ws-1", workspace_name="Workspace ws-1")
    clients: list[tuple[str, str | None]] = []
    saved = _token_auth_env(monkeypatch, core, clients=clients)

    monkeypatch.setattr(
        core,
        "load_credentials",
        lambda: _fake_creds(token="cw_old", cyberwave_base_url="https://dev.api.test"),
    )
    monkeypatch.setattr(
        core,
        "collect_runtime_env_overrides",
        lambda **_kwargs: {"CYBERWAVE_BASE_URL": "https://staging.api.test"},
    )

    assert core._ensure_credentials(skip_confirm=True, token="cw_new") is True
    assert clients == [("cw_new", "https://staging.api.test")]
    assert saved[0].cyberwave_base_url == "https://staging.api.test"


def test_ensure_credentials_token_wins_over_the_env_var(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)
    _install_fake_auth_client(monkeypatch, workspace_uuid="ws-1", workspace_name="Workspace ws-1")
    saved = _token_auth_env(monkeypatch, core)
    monkeypatch.setattr(core, "load_credentials", lambda: None)
    monkeypatch.setenv("CYBERWAVE_API_KEY", "cw_from_env")

    assert core._ensure_credentials(skip_confirm=True, token="cw_explicit") is True
    assert [c.token for c in saved] == ["cw_explicit"]


def test_ensure_credentials_aborts_on_a_rejected_token_instead_of_prompting(monkeypatch):
    """An unattended install has nobody to answer a password prompt, and a manual
    one did not ask to authenticate as somebody else."""
    core = _load_core_module(monkeypatch)
    printed = _silence_console(monkeypatch, core)

    monkeypatch.setattr(core, "load_credentials", lambda: None)
    monkeypatch.setattr(core, "collect_runtime_env_overrides", lambda **_kwargs: {})
    monkeypatch.setattr(
        core,
        "_get_sdk_client",
        lambda token, base_url=None: SimpleNamespace(
            workspaces=SimpleNamespace(
                list=lambda: (_ for _ in ()).throw(RuntimeError("401 Unauthorized"))
            )
        ),
    )
    monkeypatch.setattr(
        core.Prompt,
        "ask",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not prompt")),
    )

    assert core._ensure_credentials(skip_confirm=True, token="cw_bad") is False
    assert any("--token was rejected" in line for line in printed)


def test_ensure_credentials_token_aborts_on_a_workspace_slug_conflict(monkeypatch):
    """CYBERWAVE_WORKSPACE_SLUG naming a workspace the token is not bound to is
    unresolvable by any other credential, so falling through would be wrong."""
    core = _load_core_module(monkeypatch)
    printed = _silence_console(monkeypatch, core)
    _install_fake_auth_client(monkeypatch, workspace_uuid="ws-1", workspace_name="Workspace ws-1")
    _token_auth_env(monkeypatch, core, workspaces=("ws-1", "ws-2"))
    monkeypatch.setattr(core, "load_credentials", lambda: None)
    monkeypatch.setenv("CYBERWAVE_WORKSPACE_SLUG", "ws-2")

    assert core._ensure_credentials(skip_confirm=True, token="cw_explicit") is False
    assert any("bound to workspace" in line for line in printed)


def test_ensure_credentials_without_a_token_still_uses_stored_credentials(monkeypatch):
    """The new branch must not disturb the default path."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    monkeypatch.setattr(core, "load_credentials", lambda: _fake_creds(email="edge@example.test"))
    monkeypatch.setattr(
        core,
        "_get_sdk_client",
        lambda *a, **kw: SimpleNamespace(workspaces=SimpleNamespace(list=lambda: [])),
    )
    monkeypatch.setattr(core, "collect_runtime_env_overrides", lambda **_kwargs: {})

    assert core._ensure_credentials(skip_confirm=True) is True


def test_preflight_uses_the_explicit_token_on_a_never_paired_device(monkeypatch):
    """Without --token the check is skipped when there are no credentials, so a
    first-ever pair pays for apt-get and the Rust build before hitting the 404."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    monkeypatch.setattr(core, "load_credentials", lambda: None)
    monkeypatch.setattr(core, "collect_runtime_env_overrides", lambda **_kwargs: {})
    monkeypatch.setattr(core, "_get_sdk_client", lambda *a, **kw: _environment_client())
    _fake_token_workspace(monkeypatch, core)

    assert core._preflight_environment_reference(_ENV_UUID, token="cw_explicit") is False


def test_preflight_prefers_the_explicit_token_over_stored_credentials(monkeypatch):
    """The stored token is not the one the install will use, so probing with it
    would check the wrong workspace."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    used: list[tuple[str, str | None]] = []
    env = SimpleNamespace(uuid=_ENV_UUID, name="Prod", workspace_uuid="ws-1")
    monkeypatch.setattr(core, "load_credentials", _fake_creds)
    monkeypatch.setattr(core, "collect_runtime_env_overrides", lambda **_kwargs: {})
    monkeypatch.setattr(
        core,
        "_get_sdk_client",
        lambda token, base_url=None: used.append((token, base_url)) or _environment_client(env),
    )
    _fake_token_workspace(monkeypatch, core)

    assert core._preflight_environment_reference(_ENV_UUID, token="cw_explicit") is True
    # The token is the explicit one, but the backend stays the device's own —
    # the preflight must probe exactly what the install will use.
    assert used == [("cw_explicit", "https://api.test")]


def test_setup_edge_core_forwards_the_token_to_the_preflight_and_setup_service(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    seen: dict[str, object] = {}

    def _record(key):
        def _hook(*args, **kwargs):
            seen[key] = kwargs.get("token")
            return True

        return _hook

    monkeypatch.setattr(core, "_preflight_environment_reference", _record("preflight_token"))
    monkeypatch.setattr(core, "setup_service", _record("service_token"))

    assert (
        core.setup_edge_core(skip_confirm=True, environment=_ENV_UUID, token="cw_explicit") is True
    )
    assert seen == {"preflight_token": "cw_explicit", "service_token": "cw_explicit"}


def test_setup_service_forwards_the_token_to_ensure_credentials(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    seen: list[object] = []
    monkeypatch.setattr(core, "_migrate_legacy_config_dir", lambda: None)
    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_is_macos", lambda: False)
    monkeypatch.setattr(core, "is_macos", lambda: False)
    monkeypatch.setattr(
        core,
        "_ensure_credentials",
        lambda *, skip_confirm, token=None: seen.append(token) or False,
    )

    assert core.setup_service(core.EDGE_CORE_SPEC, skip_confirm=True, token="cw_explicit") is False
    assert seen == ["cw_explicit"]


def test_ensure_credentials_names_the_workspace_when_the_token_has_no_email(monkeypatch):
    """API tokens carry no email, so a --token-paired device would otherwise
    greet every later run with "Logged in as None"."""
    core = _load_core_module(monkeypatch)
    printed = _silence_console(monkeypatch, core)

    monkeypatch.setattr(
        core,
        "load_credentials",
        lambda: _fake_creds(email=None, workspace_name="Test Workspace"),
    )
    monkeypatch.setattr(
        core,
        "_get_sdk_client",
        lambda *a, **kw: SimpleNamespace(workspaces=SimpleNamespace(list=lambda: [])),
    )
    monkeypatch.setattr(core, "collect_runtime_env_overrides", lambda **_kwargs: {})

    assert core._ensure_credentials(skip_confirm=True) is True
    assert any("Test Workspace" in line for line in printed)
    assert not any("None" in line for line in printed)


# --- the credential swap must not outrun the install confirmation -------------


def _setup_service_env(monkeypatch, core, *, confirm):
    """Wire up ``setup_service`` far enough to trace credential/confirm order."""
    order: list[str] = []

    monkeypatch.setattr(core, "_migrate_legacy_config_dir", lambda: None)
    monkeypatch.setattr(core, "_is_linux", lambda: False)
    monkeypatch.setattr(core, "_is_macos", lambda: False)
    monkeypatch.setattr(core, "is_macos", lambda: False)
    monkeypatch.setattr(core, "_describe_pip_install_target", lambda *a, **kw: "pkg")
    monkeypatch.setattr(
        core,
        "_ensure_credentials",
        lambda *, skip_confirm, token=None: order.append(f"credentials({token})") or True,
    )
    monkeypatch.setattr(
        core,
        "Confirm",
        SimpleNamespace(
            ask=lambda *a, **kw: order.append(f"confirm -> {confirm}") or confirm,
        ),
    )
    monkeypatch.setattr(
        core,
        "install_service_package",
        lambda *a, **kw: order.append("install") or True,
    )
    return order


def test_setup_service_confirms_before_touching_credentials(monkeypatch):
    """Declining must leave the device on its old credential, not strand it on
    the new token with an environment.json naming the old workspace."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)
    order = _setup_service_env(monkeypatch, core, confirm=False)

    assert core.setup_service(core.EDGE_CORE_SPEC, skip_confirm=False, token="cw_new") is False
    assert order == ["confirm -> False"], "nothing may be persisted before the confirm"


def test_setup_service_still_establishes_credentials_before_installing(monkeypatch):
    """Moving the confirm up must not push the login past the install."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)
    order = _setup_service_env(monkeypatch, core, confirm=True)

    core.setup_service(core.EDGE_CORE_SPEC, skip_confirm=False, token="cw_new")

    assert order.index("credentials(cw_new)") < order.index("install")
    assert order[:2] == ["confirm -> True", "credentials(cw_new)"]


def test_setup_service_skips_the_confirm_with_yes(monkeypatch):
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)
    order = _setup_service_env(monkeypatch, core, confirm=False)

    core.setup_service(core.EDGE_CORE_SPEC, skip_confirm=True, token="cw_new")

    assert "confirm -> False" not in order
    assert order[0] == "credentials(cw_new)"


# --- a token pair must not inherit the previous login's identity --------------


def test_authenticate_with_api_token_clears_a_stale_email(monkeypatch):
    """save_credentials merges, so omitting the email keeps the previous login's
    address and the device reports the wrong identity after a re-pair."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)
    _install_fake_auth_client(monkeypatch, workspace_uuid="ws-2", workspace_name="Workspace ws-2")
    saved = _token_auth_env(monkeypatch, core, workspaces=("ws-2",))

    monkeypatch.setattr(
        core, "load_credentials", lambda: _fake_creds(token="cw_old", email="alice@example.test")
    )

    assert core._ensure_credentials(skip_confirm=True, token="cw_new") is True
    assert saved[0].email == "", "an empty string clears it; None would be merged away"


def test_credentials_to_dict_can_clear_an_email():
    """``""`` has to reach the payload: a missing key means "keep the old one"."""
    from cyberwave_cli.credentials import Credentials

    assert Credentials(token="t", email="").to_dict()["email"] == ""
    assert "email" not in Credentials(token="t", email=None).to_dict()
    assert Credentials(token="t", email="a@b.test").to_dict()["email"] == "a@b.test"


def test_authenticate_with_api_token_infers_settings_from_the_resolved_backend(monkeypatch):
    """The URL may come only from stored credentials, so the overrides have to
    be collected with it or the broker settings are never inferred."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)
    _install_fake_auth_client(monkeypatch, workspace_uuid="ws-1", workspace_name="Workspace ws-1")
    saved = _token_auth_env(monkeypatch, core)

    seen: list[str | None] = []

    def _collect(*, api_url_override=None):
        seen.append(api_url_override)
        if not api_url_override:
            return {}
        return {
            "CYBERWAVE_ENVIRONMENT": "dev",
            "CYBERWAVE_MQTT_HOST": "dev.mqtt.test",
            "CYBERWAVE_MQTT_PORT": "8883",
            "CYBERWAVE_WORKER_LOG_LEVEL": "info",
        }

    monkeypatch.setattr(core, "collect_runtime_env_overrides", _collect)
    monkeypatch.setattr(
        core,
        "load_credentials",
        lambda: _fake_creds(token="cw_old", cyberwave_base_url="https://api-dev.test"),
    )

    assert core._ensure_credentials(skip_confirm=True, token="cw_new") is True
    assert seen == [None, "https://api-dev.test"]
    assert saved[0].cyberwave_mqtt_host == "dev.mqtt.test"
    assert saved[0].cyberwave_mqtt_port == "8883"
    assert saved[0].cyberwave_worker_log_level == "info"


# --- the pre-check must see the credentials, wherever they live ---------------


def test_setup_edge_core_migrates_legacy_config_before_the_preflight(monkeypatch):
    """``cyberwave pair`` skips the ``edge`` group callback and setup_service
    migrates too late, so the pre-check would no-op on a legacy-config device."""
    core = _load_core_module(monkeypatch)
    _silence_console(monkeypatch, core)

    order: list[str] = []
    monkeypatch.setattr(core, "_migrate_legacy_config_dir", lambda: order.append("migrate"))
    monkeypatch.setattr(
        core,
        "_preflight_environment_reference",
        lambda ref, **kw: order.append("preflight") or True,
    )
    monkeypatch.setattr(core, "setup_service", lambda *a, **kw: True)

    assert core.setup_edge_core(skip_confirm=True, environment=_ENV_UUID) is True
    assert order == ["migrate", "preflight"]
