import importlib
import sys
from types import ModuleType, SimpleNamespace


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


def _load_core_module(monkeypatch):
    """Import cyberwave_cli.core with lightweight cyberwave stubs."""
    cyberwave_module = ModuleType("cyberwave")

    config_module = ModuleType("cyberwave.config")
    config_module.DEFAULT_BASE_URL = "https://api.example.test"

    fingerprint_module = ModuleType("cyberwave.fingerprint")
    fingerprint_module.generate_fingerprint = lambda: "fingerprint-test"

    cyberwave_module.config = config_module
    cyberwave_module.fingerprint = fingerprint_module

    monkeypatch.setitem(sys.modules, "cyberwave", cyberwave_module)
    monkeypatch.setitem(sys.modules, "cyberwave.config", config_module)
    monkeypatch.setitem(sys.modules, "cyberwave.fingerprint", fingerprint_module)

    sys.modules.pop("cyberwave_cli.config", None)
    sys.modules.pop("cyberwave_cli.core", None)
    return importlib.import_module("cyberwave_cli.core")


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

    assert uuids == ["env-project", "env-standalone"]
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
