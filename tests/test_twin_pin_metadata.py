"""Round trip of device pins through twin metadata: what the CLI writes to the
backend and what it reads back from the downloaded twin JSON."""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from tests._core_module_loader import load_core_module, use_production_device_paths
from tests.test_camera_mapping import (
    SERIAL_A,
    TWIN_A,
    TWIN_B,
    _FakeCam,
    _patch_camera_helpers,
    _write_twin_json,
)
from tests.test_udev_pinning import BRIO, C920, MIC, _node, _tree

STABLE_VIDEO = "/dev/v4l/by-id/" + C920
STABLE_AUDIO = "/dev/snd/by-id/" + MIC


class _Backend:
    """In-memory twins that merge top-level metadata keys, as PUT /twins does."""

    def __init__(self, twins: dict[str, dict]):
        self.metadata = {uuid: dict(meta) for uuid, meta in twins.items()}
        self.calls: list[tuple[str, dict]] = []
        self.fail_for: set[str] = set()

    def update(self, twin_uuid, **kwargs):
        self.calls.append((twin_uuid, kwargs))
        if twin_uuid in self.fail_for:
            raise RuntimeError("backend rejected the update")
        self.metadata[twin_uuid].update(kwargs["metadata"])

    def download(self, config_dir) -> None:
        """What `edge install` does: rewrite <uuid>.json from the backend."""
        for uuid, meta in self.metadata.items():
            (config_dir / f"{uuid}.json").write_text(
                json.dumps({"uuid": uuid, "name": uuid[:4], "metadata": meta})
            )


@pytest.fixture
def env(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    use_production_device_paths(monkeypatch, core)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    import cyberwave_cli.credentials as credentials
    import cyberwave_cli.utils as utils

    monkeypatch.setattr(credentials, "load_credentials", lambda: SimpleNamespace(token="t"))
    monkeypatch.setattr(utils, "resolve_api_url", lambda creds=None: "https://api.test")
    backend = _Backend({TWIN_A: {}, TWIN_B: {}})

    class _Client:
        def __init__(self, **_kw):
            self.twins = backend

    monkeypatch.setattr(sys.modules["cyberwave"], "Cyberwave", _Client, raising=False)
    monkeypatch.setattr(core.console, "print", lambda *a, **k: None)
    return SimpleNamespace(core=core, backend=backend, dir=tmp_path)


def _read_all(env):
    core = env.core
    return (
        core._load_twin_camera_serials(),
        core._load_twin_camera_devices(),
        core._load_twin_audio_pins(),
    )


def test_camera_pin_is_written_then_read_back_as_a_device_pin(env):
    core = env.core
    core._persist_twin_pins({TWIN_A: core._camera_pin_metadata(C920)})

    assert env.backend.metadata[TWIN_A] == {"video_device": STABLE_VIDEO, "serial_number": ""}
    env.backend.download(env.dir)
    serials, devices, audio = _read_all(env)
    assert devices == {TWIN_A: STABLE_VIDEO}
    assert serials == {}  # the blanked serial is not a pin
    assert audio == {}


def test_audio_pin_is_written_then_read_back(env):
    core = env.core
    core._persist_twin_pins({TWIN_A: core._audio_pin_metadata(MIC)})

    assert env.backend.metadata[TWIN_A] == {"audio_device": STABLE_AUDIO, "serial_number": ""}
    env.backend.download(env.dir)
    assert core._load_twin_audio_pins() == {TWIN_A: STABLE_AUDIO}


def test_repinning_replaces_a_legacy_serial_and_keeps_other_metadata(env):
    core = env.core
    env.backend.metadata[TWIN_A] = {"serial_number": SERIAL_A, "resolution": "640x480"}
    env.backend.download(env.dir)
    assert core._load_twin_camera_serials() == {TWIN_A: SERIAL_A}

    core._persist_twin_pins({TWIN_A: core._camera_pin_metadata(C920)})

    assert env.backend.metadata[TWIN_A] == {
        "serial_number": "",
        "resolution": "640x480",  # untouched: only the keys sent are replaced
        "video_device": STABLE_VIDEO,
    }
    env.backend.download(env.dir)
    serials, devices, _ = _read_all(env)
    assert serials == {} and devices == {TWIN_A: STABLE_VIDEO}


def test_clearing_a_pin_removes_it_for_every_reader(env):
    core = env.core
    core._persist_twin_pins(
        {TWIN_A: core._camera_pin_metadata(C920), TWIN_B: core._audio_pin_metadata(MIC)}
    )
    env.backend.download(env.dir)
    assert any(_read_all(env))

    core._persist_twin_pins(
        {t: {"serial_number": "", "video_device": ""} for t in (TWIN_A, TWIN_B)}
    )
    env.backend.download(env.dir)
    serials, devices, audio = _read_all(env)
    assert serials == {} and devices == {}
    # audio_device was not in the cleared keys, so an audio-only pin keeps it.
    assert audio == {TWIN_B: STABLE_AUDIO}


def test_by_id_name_in_serial_number_is_read_and_moved_to_video_device(env):
    core = env.core
    env.backend.metadata[TWIN_A] = {"serial_number": C920}
    env.backend.download(env.dir)
    assert core._load_twin_camera_serials() == {TWIN_A: C920}

    core._persist_twin_pins({TWIN_A: core._camera_pin_metadata(C920)})
    env.backend.download(env.dir)
    serials, devices, _ = _read_all(env)
    assert serials == {} and devices == {TWIN_A: STABLE_VIDEO}


def test_only_the_documented_metadata_keys_are_ever_written(env):
    core = env.core
    core._persist_twin_pins(
        {TWIN_A: core._camera_pin_metadata(C920), TWIN_B: core._audio_pin_metadata(MIC)}
    )
    for _uuid, kwargs in env.backend.calls:
        assert set(kwargs) == {"metadata"}  # never name, capabilities, ...
        assert set(kwargs["metadata"]) <= {"serial_number", "video_device", "audio_device"}


def test_a_serial_device_key_is_neither_read_nor_written(env):
    """There is no serial_device field; serial_number / video_device / audio_device are it."""
    core = env.core
    env.backend.metadata[TWIN_A] = {"serial_device": C920}
    env.backend.download(env.dir)
    assert _read_all(env) == ({}, {}, {})

    core._persist_twin_pins({TWIN_A: core._camera_pin_metadata(C920)})
    assert env.backend.metadata[TWIN_A]["serial_device"] == C920  # left alone
    assert "serial_device" not in env.backend.calls[0][1]["metadata"]


def test_a_failed_update_does_not_stop_the_others(env):
    core = env.core
    env.backend.fail_for = {TWIN_A}
    count = core._persist_twin_pins(
        {TWIN_A: core._camera_pin_metadata(C920), TWIN_B: core._audio_pin_metadata(MIC)}
    )
    assert count == 1
    assert env.backend.metadata[TWIN_B]["audio_device"] == STABLE_AUDIO
    assert env.backend.metadata[TWIN_A] == {}


def test_no_credentials_means_no_write(env, monkeypatch):
    import cyberwave_cli.credentials as credentials

    monkeypatch.setattr(credentials, "load_credentials", lambda: None)
    assert env.core._persist_twin_pins({TWIN_A: env.core._audio_pin_metadata(MIC)}) == 0
    assert env.backend.calls == []
    monkeypatch.setattr(credentials, "load_credentials", lambda: SimpleNamespace(token=""))
    assert env.core._persist_twin_pins({TWIN_A: env.core._audio_pin_metadata(MIC)}) == 0
    assert env.backend.calls == []


def test_empty_pins_never_reach_the_backend(env):
    assert env.core._persist_twin_pins({}) == 0
    assert env.core._persist_twin_pins({TWIN_A: {}}) == 0
    assert env.backend.calls == []


def test_reinstall_round_trip_reuses_what_the_first_install_wrote(env, monkeypatch):
    """First install pins by hand; after uninstall the pins come back from the backend."""
    core = env.core
    node0, node1 = _node(env.dir, "video0"), _node(env.dir, "video1")
    _tree(monkeypatch, core, env.dir, "_V4L_BY_ID_DIR", {C920: node0, BRIO: node1})
    by_id = core._V4L_BY_ID_DIR
    for twin, name in ((TWIN_A, "Cam A"), (TWIN_B, "Cam B")):
        _write_twin_json(env.dir, twin, name, camera=True)
    cams = [_FakeCam(0, "C920", node0), _FakeCam(1, "Brio", node1)]

    real_persist = core._persist_twin_pins
    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=[])
    monkeypatch.setattr(core, "_persist_twin_pins", real_persist)  # write to the fake backend
    answers = iter([1])
    monkeypatch.setattr(core, "_prompt_camera_index", lambda *a, **k: next(answers))
    core._detect_and_select_cameras()  # Cam A asked (Brio), Cam B settled by elimination
    first = json.loads((env.dir / "cameras.json").read_text())["twin_to_device"]
    assert first == {TWIN_A: f"{by_id}/{BRIO}", TWIN_B: f"{by_id}/{C920}"}
    assert env.backend.metadata[TWIN_A]["video_device"] == f"{by_id}/{BRIO}"
    assert env.backend.metadata[TWIN_B]["video_device"] == f"{by_id}/{C920}"

    for path in env.dir.glob("*.json"):
        path.unlink()
    for twin, name in ((TWIN_A, "Cam A"), (TWIN_B, "Cam B")):
        _write_twin_json(env.dir, twin, name, camera=True)
    env.backend.download(env.dir)  # adds the pins to the twin files
    for twin in (TWIN_A, TWIN_B):
        data = json.loads((env.dir / f"{twin}.json").read_text())
        data["asset"] = {"universal_schema": {"sensors": [{"type": "camera", "id": "c"}]}}
        (env.dir / f"{twin}.json").write_text(json.dumps(data))

    asked: list[str] = []
    monkeypatch.setattr(core.Confirm, "ask", lambda p, **k: asked.append(p) or True)

    def _boom(*_a, **_k):
        raise AssertionError("saved pins must bind without a menu")

    monkeypatch.setattr(core, "_prompt_camera_index", _boom)
    core._detect_and_select_cameras()

    assert asked == ["Keep these existing camera bindings?"]
    second = json.loads((env.dir / "cameras.json").read_text())["twin_to_device"]
    assert second == first


def test_payloads_are_accepted_by_the_real_sdk_schema():
    """The same metadata the CLI sends validates against the SDK's twin schema."""
    schema = pytest.importorskip("cyberwave.rest").TwinCreateSchema
    for sent in (
        {"video_device": STABLE_VIDEO, "serial_number": ""},
        {"serial_number": MIC, "audio_device": STABLE_AUDIO},
        {"serial_number": "", "video_device": ""},
    ):
        assert schema(metadata=sent).to_dict() == {"metadata": sent}


def test_audio_by_id_name_in_serial_number_is_read_and_moved_to_audio_device(env):
    core = env.core
    env.backend.metadata[TWIN_A] = {"serial_number": MIC, "audio_device": STABLE_AUDIO}
    env.backend.download(env.dir)
    assert core._load_twin_audio_pins() == {TWIN_A: STABLE_AUDIO}
    assert core._load_twin_audio_serials() == {}

    core._persist_twin_pins({TWIN_A: core._audio_pin_metadata(MIC)})

    assert env.backend.metadata[TWIN_A] == {"serial_number": "", "audio_device": STABLE_AUDIO}


def test_operator_audio_serial_is_a_serial_not_a_pin(env):
    core = env.core
    env.backend.metadata[TWIN_A] = {"serial_number": "00026"}
    env.backend.download(env.dir)

    assert core._load_twin_audio_serials() == {TWIN_A: "00026"}
    assert core._load_twin_audio_pins() == {}


def _write_local(env, uuid_, metadata):
    (env.dir / f"{uuid_}.json").write_text(
        json.dumps({"uuid": uuid_, "name": "T", "metadata": metadata})
    )


def _read_local(env, uuid_):
    return json.loads((env.dir / f"{uuid_}.json").read_text())


def test_a_saved_pin_is_mirrored_into_the_local_twin_file(env):
    """A running edge-core would otherwise push the stale downloaded file over the new pin."""
    core = env.core
    old = {"video_device": "/dev/v4l/by-id/usb-old-video-index0", "serial_number": "", "keep": 1}
    _write_local(env, TWIN_A, old)

    core._persist_twin_pins({TWIN_A: core._camera_pin_metadata(C920)})

    local = _read_local(env, TWIN_A)
    assert local["metadata"] == {"video_device": STABLE_VIDEO, "serial_number": "", "keep": 1}
    assert local["uuid"] == TWIN_A and local["name"] == "T"
    assert list(env.dir.glob(".*.tmp")) == []


def test_every_ordering_of_download_pin_and_edge_core_push_ends_on_the_new_pin(env):
    core = env.core
    _write_local(env, TWIN_A, {"video_device": "/dev/v4l/by-id/usb-old-video-index0"})
    core._persist_twin_pins({TWIN_A: core._camera_pin_metadata(C920)})

    env.backend.metadata[TWIN_A].update(_read_local(env, TWIN_A)["metadata"])

    assert env.backend.metadata[TWIN_A]["video_device"] == STABLE_VIDEO


def test_a_failed_update_leaves_the_local_twin_file_untouched(env):
    core = env.core
    old = {"video_device": "/dev/v4l/by-id/usb-old-video-index0"}
    _write_local(env, TWIN_A, old)
    before = (env.dir / f"{TWIN_A}.json").read_text()
    env.backend.fail_for = {TWIN_A}

    assert core._persist_twin_pins({TWIN_A: core._camera_pin_metadata(C920)}) == 0

    assert (env.dir / f"{TWIN_A}.json").read_text() == before


def test_a_missing_or_corrupt_local_twin_file_never_blocks_the_pin(env):
    core = env.core
    (env.dir / f"{TWIN_B}.json").write_text("{not json")

    count = core._persist_twin_pins(
        {TWIN_A: core._camera_pin_metadata(C920), TWIN_B: core._audio_pin_metadata(MIC)}
    )

    assert count == 2
    assert not (env.dir / f"{TWIN_A}.json").exists()  # nothing invented
    assert (env.dir / f"{TWIN_B}.json").read_text() == "{not json"


def test_a_local_twin_file_without_metadata_gets_the_pin(env):
    core = env.core
    (env.dir / f"{TWIN_A}.json").write_text(json.dumps({"uuid": TWIN_A}))

    core._persist_twin_pins({TWIN_A: core._audio_pin_metadata(MIC)})

    assert _read_local(env, TWIN_A)["metadata"] == {
        "audio_device": STABLE_AUDIO,
        "serial_number": "",
    }
