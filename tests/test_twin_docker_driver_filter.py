"""Tests for _twin_has_docker_driver and the filtering it applies in _select_connected_twins."""

from types import SimpleNamespace

from tests._core_module_loader import load_core_module as _load_core_module


# --- _twin_has_docker_driver unit tests ---


def test_twin_with_docker_driver_returns_true(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(
        uuid="twin-1",
        metadata={
            "drivers": {
                "default": {"docker_image": "ghcr.io/example/driver:latest"},
            },
        },
    )
    assert core._twin_has_docker_driver(twin) is True


def test_twin_with_multiple_drivers_one_docker(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(
        uuid="twin-1",
        metadata={
            "drivers": {
                "android": {"android": "com.example.driver"},
                "docker": {"docker_image": "ghcr.io/example/driver:latest"},
            },
        },
    )
    assert core._twin_has_docker_driver(twin) is True


def test_twin_with_only_android_driver_returns_false(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(
        uuid="twin-1",
        metadata={
            "drivers": {
                "default": {"android": "com.example.driver"},
            },
        },
    )
    assert core._twin_has_docker_driver(twin) is False


def test_twin_with_no_drivers_key_returns_false(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(uuid="twin-1", metadata={"edge_fingerprint": "fp-123"})
    assert core._twin_has_docker_driver(twin) is False


def test_twin_with_no_metadata_returns_false(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(uuid="twin-1")
    assert core._twin_has_docker_driver(twin) is False


def test_twin_with_none_metadata_returns_false(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(uuid="twin-1", metadata=None)
    assert core._twin_has_docker_driver(twin) is False


def test_twin_with_non_dict_metadata_returns_false(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(uuid="twin-1", metadata="not-a-dict")
    assert core._twin_has_docker_driver(twin) is False


def test_twin_with_empty_drivers_dict_returns_false(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(uuid="twin-1", metadata={"drivers": {}})
    assert core._twin_has_docker_driver(twin) is False


def test_twin_with_non_dict_drivers_returns_false(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(uuid="twin-1", metadata={"drivers": "invalid"})
    assert core._twin_has_docker_driver(twin) is False


def test_twin_with_non_dict_driver_entry_returns_false(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(uuid="twin-1", metadata={"drivers": {"default": "not-a-dict"}})
    assert core._twin_has_docker_driver(twin) is False


def test_twin_with_services_driver_returns_true(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(
        uuid="twin-1",
        metadata={
            "drivers": {
                "default": {
                    "services": {
                        "driver": {"docker_image": "img:latest"},
                        "sidecar": {"docker_image": "sidecar:latest"},
                    },
                },
            },
        },
    )
    assert core._twin_has_docker_driver(twin) is True


def test_twin_with_services_and_no_docker_image_still_matches(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(
        uuid="twin-1",
        metadata={
            "drivers": {
                "default": {"services": {"a": {}, "b": {}}},
            },
        },
    )
    assert core._twin_has_docker_driver(twin) is True


def test_twin_with_multiple_drivers_one_services(monkeypatch):
    core = _load_core_module(monkeypatch)
    twin = SimpleNamespace(
        uuid="twin-1",
        metadata={
            "drivers": {
                "android": {"android": "com.example"},
                "multi": {"services": {"a": {"docker_image": "img:latest"}}},
            },
        },
    )
    assert core._twin_has_docker_driver(twin) is True


# --- _select_connected_twins filtering tests ---


class _FakeTwinsManager:
    def __init__(self, twins):
        self._twins = twins

    def list(self, environment_id=None):
        return list(self._twins)


def test_select_connected_twins_filters_out_non_docker_twins(monkeypatch):
    core = _load_core_module(monkeypatch)

    docker_twin = SimpleNamespace(
        uuid="twin-docker",
        name="DockerBot",
        asset_uuid="asset-1",
        metadata={"drivers": {"default": {"docker_image": "img:latest"}}},
    )
    services_twin = SimpleNamespace(
        uuid="twin-services",
        name="MultiBot",
        asset_uuid="asset-4",
        metadata={"drivers": {"default": {"services": {"a": {"docker_image": "img:v2"}}}}},
    )
    android_twin = SimpleNamespace(
        uuid="twin-android",
        name="AndroidBot",
        asset_uuid="asset-2",
        metadata={"drivers": {"default": {"android": "com.example"}}},
    )
    no_drivers_twin = SimpleNamespace(
        uuid="twin-bare",
        name="BareBot",
        asset_uuid="asset-3",
        metadata={},
    )

    client = SimpleNamespace(
        twins=_FakeTwinsManager([docker_twin, services_twin, android_twin, no_drivers_twin])
    )

    monkeypatch.setattr(
        core,
        "_select_multiple_with_arrows",
        lambda _title, _labels: [0, 1],
    )

    result = core._select_connected_twins(client, "env-1", skip_confirm=False)
    assert result == ["twin-docker", "twin-services"]


def test_select_connected_twins_skip_confirm_picks_first_docker_twin(monkeypatch):
    core = _load_core_module(monkeypatch)

    android_twin = SimpleNamespace(
        uuid="twin-android",
        name="AndroidBot",
        asset_uuid="asset-2",
        metadata={"drivers": {"default": {"android": "com.example"}}},
    )
    docker_twin = SimpleNamespace(
        uuid="twin-docker",
        name="DockerBot",
        asset_uuid="asset-1",
        metadata={"drivers": {"default": {"docker_image": "img:latest"}}},
    )

    client = SimpleNamespace(twins=_FakeTwinsManager([android_twin, docker_twin]))

    result = core._select_connected_twins(client, "env-1", skip_confirm=True)
    assert result == ["twin-docker"]


def test_select_connected_twins_returns_empty_when_no_docker_twins(monkeypatch):
    core = _load_core_module(monkeypatch)

    android_twin = SimpleNamespace(
        uuid="twin-android",
        name="AndroidBot",
        asset_uuid="asset-2",
        metadata={"drivers": {"default": {"android": "com.example"}}},
    )

    client = SimpleNamespace(twins=_FakeTwinsManager([android_twin]))

    result = core._select_connected_twins(client, "env-1", skip_confirm=False)
    assert result == []


def test_select_connected_twins_returns_empty_when_no_twins(monkeypatch):
    core = _load_core_module(monkeypatch)
    client = SimpleNamespace(twins=_FakeTwinsManager([]))

    result = core._select_connected_twins(client, "env-1", skip_confirm=False)
    assert result == []
