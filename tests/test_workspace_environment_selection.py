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
    monkeypatch.setattr(core, "collect_runtime_env_overrides", lambda: {})

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
