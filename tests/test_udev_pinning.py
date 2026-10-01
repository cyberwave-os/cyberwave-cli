"""Tests for pinning camera and audio twins to udev ``by-id`` names.

The ``by-id`` trees are redirected into ``tmp_path`` so no real ``/dev`` is read.
"""
from __future__ import annotations

import json

from tests._core_module_loader import load_core_module
from tests.test_camera_mapping import (
    SERIAL_A,
    TWIN_A,
    TWIN_B,
    TWIN_C,
    _FakeCam,
    _patch_camera_helpers,
    _write_twin_json,
)

C920 = "usb-046d_HD_Pro_Webcam_C920_8E3A1B2F-video-index0"
BRIO = "usb-046d_Brio_100_1234ABCD-video-index0"
MIC = "usb-miniDSP_UMA16v2_00026-00"
HEADSET = "usb-Jabra_Evolve_65_ABC123-00"


def _tree(monkeypatch, core, tmp_path, attr: str, links: dict[str, str]) -> str:
    directory = tmp_path / attr.strip("_").lower()
    directory.mkdir()
    for name, target in links.items():
        (directory / name).symlink_to(target)
    monkeypatch.setattr(core, attr, str(directory))
    return str(directory)


def _node(tmp_path, name: str) -> str:
    node = tmp_path / name
    node.write_text("")
    return str(node)


# ---- listing -----------------------------------------------------------------


def test_list_v4l_by_id_keeps_live_capture_nodes_only(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    live = _node(tmp_path, "video0")
    meta = _node(tmp_path, "video1")
    _tree(
        monkeypatch,
        core,
        tmp_path,
        "_V4L_BY_ID_DIR",
        {
            C920: live,
            C920.replace("index0", "index1"): meta,  # metadata node
            BRIO: str(tmp_path / "unplugged"),  # dangling
        },
    )

    assert [d.name for d in core._list_v4l_by_id()] == [C920]


def test_list_alsa_by_id_and_missing_directory(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "_ALSA_BY_ID_DIR", str(tmp_path / "absent"))
    assert core._list_alsa_by_id() == []

    _tree(monkeypatch, core, tmp_path, "_ALSA_BY_ID_DIR", {MIC: _node(tmp_path, "controlC2")})
    assert [d.name for d in core._list_alsa_by_id()] == [MIC]


def test_udev_pin_is_connected_accepts_name_or_full_path(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    directory = _tree(
        monkeypatch, core, tmp_path, "_ALSA_BY_ID_DIR", {MIC: _node(tmp_path, "controlC2")}
    )
    assert core._udev_pin_is_connected(MIC, directory)
    assert core._udev_pin_is_connected(f"{directory}/{MIC}", directory)
    assert not core._udev_pin_is_connected(HEADSET, directory)
    assert not core._udev_pin_is_connected("../escape", directory)
    assert not core._udev_pin_is_connected("", directory)


# ---- cameras -----------------------------------------------------------------


def test_manual_camera_choice_is_pinned_as_its_by_id_name(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node1})

    cameras = [
        _FakeCam(0, "Other", node0, serial=SERIAL_A),
        _FakeCam(1, "C920", node1),  # no USB serial: only udev can name it
    ]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["1"])
    recorded: dict[str, str] = {}
    monkeypatch.setattr(core, "_persist_twin_serial_numbers", recorded.update)

    core._detect_and_select_cameras()

    # The by-id name wins over the descriptor serial and works for a camera
    # that reports none.
    assert recorded == {TWIN_A: C920}


def test_camera_menu_shows_the_by_id_name(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    node = _node(tmp_path, "video0")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node})
    assert core._camera_identity_hint(_FakeCam(0, "C920", node)) == f"id {C920}"


def test_saved_by_id_pin_binds_without_prompting(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: "/dev/video6"})
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=C920)

    cameras = [_FakeCam(0, "Other", "/dev/video0"), _FakeCam(6, "C920", "/dev/video6")]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])

    def _boom(*_a, **_k):
        raise AssertionError("prompted for a valid saved pin")

    monkeypatch.setattr(core, "_prompt_camera_index", _boom)

    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    # The stable path, not an index; and the by-id name given as a serial moves
    # to video_device because the camera driver cannot resolve it as a serial.
    assert data["twin_to_device"] == {TWIN_A: f"{core._V4L_BY_ID_DIR}/{C920}"}
    assert pinned == {TWIN_A: core._camera_pin_metadata(C920)}


def test_saved_by_id_pin_survives_a_renumbered_node(monkeypatch, tmp_path):
    """The kernel moved the camera from video6 to video2; the name still finds it."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: "/dev/video2"})
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=C920)

    cameras = [_FakeCam(0, "Other", "/dev/video0"), _FakeCam(2, "C920", "/dev/video2")]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=[])

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: f"{core._V4L_BY_ID_DIR}/{C920}"}


def test_disconnected_by_id_pin_forces_a_new_choice_and_is_replaced(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {BRIO: node1})
    # Saved pin names a camera that is no longer attached.
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=C920)

    cameras = [_FakeCam(0, "Other", node0, serial=SERIAL_A), _FakeCam(1, "Brio", node1)]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["1"])
    recorded: dict[str, str] = {}
    monkeypatch.setattr(core, "_persist_twin_serial_numbers", recorded.update)

    core._detect_and_select_cameras()

    assert recorded == {TWIN_A: BRIO}


def test_depth_twin_with_absent_by_id_pin_is_not_waved_through(monkeypatch, tmp_path):
    """A by-id name is checkable on this host, unlike a librealsense serial."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {})
    _write_twin_json(tmp_path, TWIN_A, "Depth", camera=True, depth=True, serial=C920)

    cameras = [_FakeCam(0, "A", node0), _FakeCam(1, "B", node1)]
    _patch_camera_helpers(monkeypatch, core, cameras, prompt_inputs=["0"])
    monkeypatch.setattr(core, "_persist_twin_serial_numbers", lambda _m: 0)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 0}  # it was asked, not deferred


# ---- identical cameras ------------------------------------------------------


def test_identical_cameras_sharing_one_link_are_unidentifiable(monkeypatch, tmp_path):
    """Two C920s without a USB serial collapse onto a single by-id link."""
    core = load_core_module(monkeypatch)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node1})  # video0 lost

    cams = [_FakeCam(0, "C920", node0), _FakeCam(1, "C920", node1)]
    assert core._unidentifiable_cameras(cams) == {0, 1}


def test_identical_cameras_with_distinct_links_are_fine(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node0, BRIO: node1})

    cams = [_FakeCam(0, "C920", node0), _FakeCam(1, "C920", node1)]
    assert core._unidentifiable_cameras(cams) == set()


def test_unidentifiable_camera_is_selectable_but_never_pinned(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node1})

    cams = [_FakeCam(0, "C920", node0), _FakeCam(1, "C920", node1)]
    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=["1"])
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)

    core._detect_and_select_cameras()

    assert pinned == {}  # nothing written for a coin-flip identity
    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 1}  # positional, this run only


# ---- what gets written -------------------------------------------------------


def test_camera_pin_metadata_uses_video_device_and_blanks_a_stale_serial(monkeypatch):
    core = load_core_module(monkeypatch)
    assert core._camera_pin_metadata(C920) == {
        "video_device": f"/dev/v4l/by-id/{C920}",
        "serial_number": "",
    }
    # A hardware serial (depth / legacy) keeps the old contract.
    assert core._camera_pin_metadata(SERIAL_A) == {"serial_number": SERIAL_A}


def test_audio_pin_metadata_sets_identity_and_source(monkeypatch):
    core = load_core_module(monkeypatch)
    assert core._audio_pin_metadata(MIC) == {
        "serial_number": MIC,
        "audio_device": f"/dev/snd/by-id/{MIC}",
    }


def test_persist_twin_pins_sends_exactly_the_given_metadata(monkeypatch):
    import sys
    from types import SimpleNamespace

    core = load_core_module(monkeypatch)
    import cyberwave_cli.credentials as credentials
    import cyberwave_cli.utils as utils

    monkeypatch.setattr(credentials, "load_credentials", lambda: SimpleNamespace(token="t"))
    monkeypatch.setattr(utils, "resolve_api_url", lambda creds=None: "https://api.test")
    calls: list[tuple] = []

    class _Twins:
        def update(self, twin_uuid, metadata=None):
            calls.append((twin_uuid, metadata))

    class _Client:
        def __init__(self, **_kw):
            self.twins = _Twins()

    monkeypatch.setattr(sys.modules["cyberwave"], "Cyberwave", _Client, raising=False)

    count = core._persist_twin_pins(
        {TWIN_A: core._camera_pin_metadata(C920), TWIN_B: core._audio_pin_metadata(MIC)}
    )

    assert count == 2
    assert calls == [
        (TWIN_A, {"video_device": f"/dev/v4l/by-id/{C920}", "serial_number": ""}),
        (TWIN_B, {"serial_number": MIC, "audio_device": f"/dev/snd/by-id/{MIC}"}),
    ]


def test_manual_camera_choice_stores_the_stable_path_in_cameras_json(monkeypatch, tmp_path):
    """A positional index would outrank, and so undo, the pin just written."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node1, BRIO: node0})

    cams = [_FakeCam(0, "Brio", node0), _FakeCam(1, "C920", node1)]
    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=["1"])
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: f"{core._V4L_BY_ID_DIR}/{C920}"}
    assert pinned == {TWIN_A: core._camera_pin_metadata(C920)}


# ---- automatic association (cameras) -----------------------------------------


def test_one_twin_one_camera_is_associated_and_pinned_without_asking(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    node = _node(tmp_path, "video0")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node})

    _patch_camera_helpers(monkeypatch, core, [_FakeCam(0, "C920", node)], prompt_inputs=[])
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)

    def _boom(*_a, **_k):
        raise AssertionError("nothing to disambiguate, nothing to ask")

    monkeypatch.setattr(core, "_prompt_camera_index", _boom)
    monkeypatch.setattr(core.Confirm, "ask", _boom)

    core._detect_and_select_cameras()

    assert pinned == {TWIN_A: core._camera_pin_metadata(C920)}


def test_two_twins_one_camera_shares_it_without_pinning_either(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)
    node = _node(tmp_path, "video0")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node})

    _patch_camera_helpers(monkeypatch, core, [_FakeCam(0, "C920", node)], prompt_inputs=[])
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)

    core._detect_and_select_cameras()

    assert pinned == {}  # which twin owns it is exactly the unknown


def test_stale_pin_with_one_camera_needs_approval_to_substitute(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=BRIO)  # gone
    node = _node(tmp_path, "video0")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node})

    _patch_camera_helpers(monkeypatch, core, [_FakeCam(0, "C920", node)], prompt_inputs=[])
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)

    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: False)
    core._detect_and_select_cameras()
    assert pinned == {}

    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: True)
    core._detect_and_select_cameras()
    assert pinned == {TWIN_A: core._camera_pin_metadata(C920)}


# ---- audio -------------------------------------------------------------------

CARD_MIC = ("046d:0825", "UMA16v2", True, False)
CARD_HEADSET = ("0b0e:245e", "Evolve65", True, True)
CARD_SPEAKER = ("1234:5678", "Boombox", False, True)


def _write_audio_twin(config_dir, twin_uuid, name, *, sensor="microphone", serial=None, extra=None):
    kinds = [sensor] if isinstance(sensor, str) else sensor
    sensors = [{"type": kind, "id": f"{kind}0"} for kind in kinds]
    payload = {
        "uuid": twin_uuid,
        "name": name,
        "asset": {"universal_schema": {"sensors": sensors}},
    }
    metadata = dict(extra or {})
    if serial is not None:
        metadata["serial_number"] = serial
    if metadata:
        payload["metadata"] = metadata
    (config_dir / f"{twin_uuid}.json").write_text(json.dumps(payload))


def _audio_env(monkeypatch, core, tmp_path, cards, *, confirm=True, answers=()):
    """Fake ``/proc/asound`` + ``/dev/snd/by-id``.

    *cards* is a list of ``(usbid, label, capture, playback, link_name | None)``.
    """
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")
    monkeypatch.setattr(core.platform, "system", lambda: "Linux")
    proc = tmp_path / "asound"
    proc.mkdir()
    links: dict[str, str] = {}
    for n, (usbid, label, capture, playback, link) in enumerate(cards):
        card = proc / f"card{n}"
        card.mkdir()
        (card / "usbid").write_text(usbid + "\n")
        (card / "id").write_text(label + "\n")
        if capture:
            (card / "pcm0c").mkdir()
        if playback:
            (card / "pcm0p").mkdir()
        if link:
            links[link] = _node(tmp_path, f"controlC{n}")
    monkeypatch.setattr(core, "_PROC_ASOUND_DIR", str(proc))
    _tree(monkeypatch, core, tmp_path, "_ALSA_BY_ID_DIR", links)
    monkeypatch.setattr(core.console, "print", lambda *a, **k: None)

    asked: list[str] = []
    answer_iter = iter(answers)

    def _ask(prompt, **_k):
        asked.append(prompt)
        return next(answer_iter)

    monkeypatch.setattr(core.Prompt, "ask", _ask)
    confirms: list[str] = []

    def _confirm(prompt, **_k):
        confirms.append(prompt)
        return confirm

    monkeypatch.setattr(core.Confirm, "ask", _confirm)
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)
    return asked, confirms, pinned


def _card(spec, link):
    return (*spec[:1], spec[1], spec[2], spec[3], link)


def test_audio_card_discovery_reads_capabilities_and_links(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_HEADSET, HEADSET), _card(CARD_SPEAKER, None)],
    )
    cards = core._discover_usb_audio_cards()
    assert [(c.index, c.usbid, c.link, c.capture, c.playback) for c in cards] == [
        (0, "0b0e:245e", HEADSET, True, True),
        (1, "1234:5678", None, False, True),
    ]


def test_audio_non_usb_cards_are_ignored(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _audio_env(monkeypatch, core, tmp_path, [])
    onboard = tmp_path / "asound" / "card9"
    onboard.mkdir()
    (onboard / "id").write_text("Headphones")
    assert core._discover_usb_audio_cards() == []


def test_audio_one_twin_one_device_is_paired_without_asking(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic")
    asked, confirms, pinned = _audio_env(
        monkeypatch, core, tmp_path, [_card(CARD_MIC, MIC)]
    )

    core._detect_and_select_audio_devices()

    assert asked == [] and confirms == []
    assert pinned == {TWIN_A: core._audio_pin_metadata(MIC)}


def test_audio_several_devices_ask_which_one(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic")
    asked, _c, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_HEADSET, HEADSET), _card(CARD_MIC, MIC)],
        answers=["1"],  # candidates keep card order: headset=0, mic=1
    )

    core._detect_and_select_audio_devices()

    assert asked == ["Audio device for Mic"]
    assert pinned == {TWIN_A: core._audio_pin_metadata(MIC)}


def test_audio_a_microphone_twin_never_gets_a_playback_only_device(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic")
    asked, _c, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_SPEAKER, MIC), _card(CARD_MIC, HEADSET)],
    )

    core._detect_and_select_audio_devices()

    assert asked == []  # the speaker is not a candidate, so one device remains
    assert pinned == {TWIN_A: core._audio_pin_metadata(HEADSET)}


def test_audio_mic_and_speaker_twins_each_get_their_own_device(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic")
    _write_audio_twin(tmp_path, TWIN_B, "Speaker", sensor="speaker")
    asked, confirms, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_MIC, MIC), _card(CARD_SPEAKER, HEADSET)],
    )

    core._detect_and_select_audio_devices()

    assert asked == [] and confirms == []
    assert pinned == {
        TWIN_A: core._audio_pin_metadata(MIC),
        TWIN_B: core._audio_pin_metadata(HEADSET),
    }


def test_audio_two_twins_one_device_asks_to_share(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic A")
    _write_audio_twin(tmp_path, TWIN_B, "Mic B")
    _asked, confirms, pinned = _audio_env(
        monkeypatch, core, tmp_path, [_card(CARD_MIC, MIC)]
    )

    core._detect_and_select_audio_devices()

    assert len(confirms) == 1 and "Use it for all of them?" in confirms[0]
    assert set(pinned) == {TWIN_A, TWIN_B}


def test_audio_declining_to_share_pins_nothing(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic A")
    _write_audio_twin(tmp_path, TWIN_B, "Mic B")
    _a, _c, pinned = _audio_env(
        monkeypatch, core, tmp_path, [_card(CARD_MIC, MIC)], confirm=False
    )

    core._detect_and_select_audio_devices()

    assert pinned == {}


def test_audio_two_twins_two_devices_asks_per_twin(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic A")
    _write_audio_twin(tmp_path, TWIN_B, "Mic B")
    asked, _c, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_HEADSET, HEADSET), _card(CARD_MIC, MIC)],
        answers=["1", "0"],
    )

    core._detect_and_select_audio_devices()

    assert asked == ["Audio device for Mic A", "Audio device for Mic B"]
    assert pinned == {
        TWIN_A: core._audio_pin_metadata(MIC),
        TWIN_B: core._audio_pin_metadata(HEADSET),
    }


def test_audio_connected_pin_is_reused_without_asking(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic", serial=MIC)
    asked, confirms, pinned = _audio_env(
        monkeypatch, core, tmp_path, [_card(CARD_MIC, MIC)]
    )

    core._detect_and_select_audio_devices()

    assert asked == []
    assert confirms == ["Keep these existing audio bindings?"]
    assert pinned == {}


def test_audio_pin_in_audio_device_field_is_recognised(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _audio_env(monkeypatch, core, tmp_path, [_card(CARD_MIC, MIC)])
    _write_audio_twin(
        tmp_path, TWIN_A, "Mic", extra={"audio_device": f"{core._ALSA_BY_ID_DIR}/{MIC}"}
    )
    assert core._load_twin_audio_pins() == {TWIN_A: MIC}


def test_audio_declining_connected_pin_reasks(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic", serial=MIC)
    asked, _c, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_HEADSET, HEADSET), _card(CARD_MIC, MIC)],
        confirm=False,
        answers=["0"],
    )

    core._detect_and_select_audio_devices()

    assert asked == ["Audio device for Mic"]
    assert pinned == {TWIN_A: core._audio_pin_metadata(HEADSET)}


def test_audio_disconnected_pin_is_invalid_and_forces_a_choice(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic", serial=MIC)  # MIC is unplugged
    asked, confirms, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_HEADSET, HEADSET), _card(CARD_SPEAKER, None)],
        answers=[],
    )

    core._detect_and_select_audio_devices()

    # Exactly one capable device remains, but the twin's own pin did not
    # resolve, so the substitute needs the operator's approval.
    assert len(confirms) == 1 and "is not connected" in confirms[0]
    assert pinned == {TWIN_A: core._audio_pin_metadata(HEADSET)}


def test_audio_declined_substitute_for_a_stale_pin_changes_nothing(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic", serial=MIC)
    _a, _c, pinned = _audio_env(
        monkeypatch, core, tmp_path, [_card(CARD_HEADSET, HEADSET)], confirm=False
    )

    core._detect_and_select_audio_devices()

    assert pinned == {}


def test_audio_identical_units_without_distinct_ids_are_never_pinned(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic A")
    _write_audio_twin(tmp_path, TWIN_B, "Mic B")
    # Two identical dongles; udev kept a single link for the pair.
    asked, _c, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_MIC, MIC), _card(CARD_MIC, None)],
    )

    core._detect_and_select_audio_devices()

    assert core._unidentifiable_usbids(core._discover_usb_audio_cards()) == {"046d:0825"}
    assert asked == []
    assert pinned == {}


def test_audio_identical_units_with_distinct_ids_are_both_usable(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic A")
    _write_audio_twin(tmp_path, TWIN_B, "Mic B")
    other = "usb-miniDSP_UMA16v2_00027-00"
    asked, _c, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_MIC, MIC), _card(CARD_MIC, other)],
        answers=["1", "0"],
    )

    core._detect_and_select_audio_devices()

    assert asked == ["Audio device for Mic A", "Audio device for Mic B"]
    assert pinned == {
        TWIN_A: core._audio_pin_metadata(other),
        TWIN_B: core._audio_pin_metadata(MIC),
    }


def test_audio_no_devices_pins_nothing(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic")
    asked, _c, pinned = _audio_env(monkeypatch, core, tmp_path, [])

    core._detect_and_select_audio_devices()

    assert asked == [] and pinned == {}


def test_audio_invalid_answer_skips_the_twin(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic")
    _a, _c, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_HEADSET, HEADSET), _card(CARD_MIC, MIC)],
        answers=["9"],
    )

    core._detect_and_select_audio_devices()

    assert pinned == {}


def test_audio_is_a_no_op_off_linux_and_without_audio_twins(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic")
    asked, _c, pinned = _audio_env(monkeypatch, core, tmp_path, [_card(CARD_MIC, MIC)])
    monkeypatch.setattr(core.platform, "system", lambda: "Darwin")
    core._detect_and_select_audio_devices()
    assert asked == [] and pinned == {}

    monkeypatch.setattr(core.platform, "system", lambda: "Linux")
    (tmp_path / f"{TWIN_A}.json").unlink()
    _write_twin_json(tmp_path, TWIN_B, "Arm", camera=False)
    core._detect_and_select_audio_devices()
    assert asked == [] and pinned == {}


# ---- generic inference -------------------------------------------------------


def _any(_twin, _device):
    return True


def test_inference_decides_a_lone_twin_with_a_lone_device(monkeypatch):
    core = load_core_module(monkeypatch)
    result = core._infer_associations(["t"], ["d"], claimed=set(), compatible=_any)
    assert result.assigned["t"][0] == "d" and result.unresolved == []


def test_inference_leaves_identical_candidates_to_the_operator(monkeypatch):
    core = load_core_module(monkeypatch)
    result = core._infer_associations(["a", "b"], ["x", "y"], claimed=set(), compatible=_any)
    assert result.assigned == {} and result.unresolved == ["a", "b"]


def test_inference_eliminates_devices_claimed_by_verified_pins(monkeypatch):
    core = load_core_module(monkeypatch)
    result = core._infer_associations(["b"], ["x", "y"], claimed={"x"}, compatible=_any)
    assert result.assigned["b"][0] == "y"
    assert "left" in result.assigned["b"][1]


def test_inference_uses_sensor_type_to_split_otherwise_identical_pairs(monkeypatch):
    core = load_core_module(monkeypatch)
    can = {("mic", "usb_mic"), ("spk", "usb_spk"), ("mic", "headset"), ("spk", "headset")}
    result = core._infer_associations(
        ["mic", "spk"],
        ["usb_mic", "usb_spk"],
        claimed=set(),
        compatible=lambda t, d: (t, d) in can,
    )
    assert {t: d for t, (d, _w) in result.assigned.items()} == {"mic": "usb_mic", "spk": "usb_spk"}


def test_inference_chains_eliminations(monkeypatch):
    """a only fits x, so b is left with y, which settles c on z."""
    core = load_core_module(monkeypatch)
    fits = {"a": {"x"}, "b": {"x", "y"}, "c": {"y", "z"}}
    result = core._infer_associations(
        ["a", "b", "c"],
        ["x", "y", "z"],
        claimed=set(),
        compatible=lambda t, d: d in fits[t],
    )
    assert {t: d for t, (d, _w) in result.assigned.items()} == {"a": "x", "b": "y", "c": "z"}


def test_inference_decides_only_the_twins_all_assignments_agree_on(monkeypatch):
    core = load_core_module(monkeypatch)
    fits = {"a": {"x"}, "b": {"y", "z"}, "c": {"y", "z"}}
    result = core._infer_associations(
        ["a", "b", "c"],
        ["x", "y", "z"],
        claimed=set(),
        compatible=lambda t, d: d in fits[t],
    )
    assert list(result.assigned) == ["a"] and result.unresolved == ["b", "c"]


def test_inference_never_invents_a_pairing_when_devices_run_short(monkeypatch):
    core = load_core_module(monkeypatch)
    result = core._infer_associations(["a", "b"], ["x"], claimed=set(), compatible=_any)
    assert result.assigned == {} and result.unresolved == ["a", "b"]


def test_inference_with_nothing_free_decides_nothing(monkeypatch):
    core = load_core_module(monkeypatch)
    result = core._infer_associations(["a"], ["x"], claimed={"x"}, compatible=_any)
    assert result.assigned == {}


def test_inference_gives_up_rather_than_explode(monkeypatch):
    core = load_core_module(monkeypatch)
    devices = list(range(12))
    result = core._infer_associations(
        ["t%d" % i for i in range(12)], devices, claimed=set(), compatible=_any, limit=100
    )
    assert result.assigned == {}


# ---- the evidence settles multi-sensor installs ------------------------------


def test_camera_verified_pin_settles_the_other_twin_among_two_cameras(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node0, BRIO: node1})
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=BRIO)  # verified
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True)  # no pin

    cams = [_FakeCam(0, "C920", node0), _FakeCam(1, "Brio", node1)]
    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=[])
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)

    def _boom(*_a, **_k):
        raise AssertionError("the evidence leaves one answer; nothing to ask")

    monkeypatch.setattr(core, "_prompt_camera_index", _boom)

    core._detect_and_select_cameras()

    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {
        TWIN_A: f"{core._V4L_BY_ID_DIR}/{BRIO}",
        TWIN_B: f"{core._V4L_BY_ID_DIR}/{C920}",
    }
    # TWIN_A holds its by-id name as a serial: migrated; TWIN_B is newly paired.
    assert pinned == {
        TWIN_A: core._camera_pin_metadata(BRIO),
        TWIN_B: core._camera_pin_metadata(C920),
    }


def test_camera_single_twin_among_many_with_one_identifiable_stays_a_question(
    monkeypatch, tmp_path
):
    """An unpinnable rival is still a rival: do not auto-pick around it."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node0})  # video1 has none

    cams = [_FakeCam(0, "C920", node0), _FakeCam(1, "Other", node1)]
    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=["0"])
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)

    core._detect_and_select_cameras()

    # Two candidates, so the operator was asked (prompt answered "0").
    assert pinned == {TWIN_A: core._camera_pin_metadata(C920)}


def test_camera_manual_answers_settle_the_remaining_twin(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    names = {0: C920, 1: BRIO, 2: "usb-Intel_RealSense_D435_RS1-video-index0"}
    nodes = {i: _node(tmp_path, f"video{i}") for i in names}
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {names[i]: nodes[i] for i in names})
    for twin, label in ((TWIN_A, "Cam A"), (TWIN_B, "Cam B"), (TWIN_C, "Cam C")):
        _write_twin_json(tmp_path, twin, label, camera=True)

    cams = [_FakeCam(i, f"Model{i}", nodes[i]) for i in names]
    asked: list[str] = []
    answers = iter(["2", "0"])

    def _prompt(_cams, valid, *, prompt, default_index):
        asked.append(prompt)
        return int(next(answers))

    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=[])
    monkeypatch.setattr(core, "_prompt_camera_index", _prompt)
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)

    core._detect_and_select_cameras()

    assert asked == ["Camera for Cam A", "Camera for Cam B"]  # Cam C needed no question
    assert set(pinned) == {TWIN_A, TWIN_B, TWIN_C}
    assert pinned[TWIN_C] == core._camera_pin_metadata(BRIO)  # the camera left over


def test_audio_verified_pin_settles_the_other_microphone(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic A", serial=MIC)
    _write_audio_twin(tmp_path, TWIN_B, "Mic B")
    asked, confirms, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_MIC, MIC), _card(CARD_HEADSET, HEADSET)],
    )

    core._detect_and_select_audio_devices()

    assert asked == []
    assert confirms == ["Keep these existing audio bindings?"]
    assert pinned == {TWIN_B: core._audio_pin_metadata(HEADSET)}


# ---- the reinstall scenario that started all this ----------------------------


def test_reinstall_with_saved_pins_is_never_silent(monkeypatch, tmp_path):
    """Pins from the first install must not bind cameras without the operator.

    This is the original report: after uninstall + install the cameras were
    associated without a single question because the backend still carried
    each twin's pin.
    """
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node0, BRIO: node1})
    c920_path = f"{core._V4L_BY_ID_DIR}/{C920}"
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, video_device=c920_path)
    brio_path = f"{core._V4L_BY_ID_DIR}/{BRIO}"
    _write_twin_json(tmp_path, TWIN_B, "Cam B", camera=True, video_device=brio_path)
    cams = [_FakeCam(0, "C920", node0), _FakeCam(1, "Brio", node1)]

    # Keep: bindings come from the pins, and the operator was asked first.
    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=[])
    confirms: list[str] = []
    monkeypatch.setattr(core.Confirm, "ask", lambda p, **k: confirms.append(p) or True)
    core._detect_and_select_cameras()
    assert confirms == ["Keep these existing camera bindings?"]

    # Decline: every twin is asked again, answers may differ from the pins.
    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=[])
    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: False)
    asked: list[str] = []
    answers = iter([1])

    def _prompt(_c, _v, *, prompt, default_index):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr(core, "_prompt_camera_index", _prompt)
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)
    core._detect_and_select_cameras()

    # Cam A is asked; Cam B is then the only camera left, so it follows.
    assert asked == ["Camera for Cam A"]
    assert pinned[TWIN_A]["video_device"].endswith(BRIO)
    assert pinned[TWIN_B]["video_device"].endswith(C920)


def test_remapping_to_an_unpinnable_camera_clears_the_old_pin(monkeypatch, tmp_path):
    """Otherwise the driver keeps opening the previous camera at runtime."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node0})  # video1: no id
    c920_path = f"{core._V4L_BY_ID_DIR}/{C920}"
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, video_device=c920_path)
    cams = [_FakeCam(0, "C920", node0), _FakeCam(1, "Plain", node1)]

    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=["1"])
    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: False)  # redo the pins
    pinned: dict = {}
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)

    core._detect_and_select_cameras()

    assert pinned == {TWIN_A: {"serial_number": "", "video_device": ""}}
    data = json.loads((tmp_path / "cameras.json").read_text())
    assert data["twin_to_device"] == {TWIN_A: 1}


# ---- compatibility with what edge-core and the microphone driver accept ------


def test_microphone_driver_selection_mirrors_edge_core(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [_card(CARD_MIC, MIC), _card(CARD_HEADSET, HEADSET)],
    )
    assert core._microphone_driver_can_select(MIC)
    assert core._microphone_driver_can_select(HEADSET)
    assert not core._microphone_driver_can_select("usb-Weird-00")  # no serial-like part
    assert not core._microphone_driver_can_select("usb-Absent_Thing_1-00")  # not attached


def test_microphone_driver_cannot_tell_two_units_apart_by_serial_token(monkeypatch, tmp_path):
    """Edge-core refuses such a pin and would hold the twin, so never offer it."""
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Mic")
    asked, _c, pinned = _audio_env(
        monkeypatch,
        core,
        tmp_path,
        [
            _card(CARD_MIC, "usb-A_Generic_USB_Audio-00"),
            _card(CARD_HEADSET, "usb-B_Generic_USB_Audio-00"),
        ],
    )

    core._detect_and_select_audio_devices()

    assert asked == [] and pinned == {}


def test_speaker_twin_does_not_need_the_microphone_serial_token(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    _write_audio_twin(tmp_path, TWIN_A, "Speaker", sensor="speaker")
    _a, _c, pinned = _audio_env(
        monkeypatch, core, tmp_path, [_card(CARD_SPEAKER, "usb-Weird-00")]
    )

    core._detect_and_select_audio_devices()

    assert pinned == {TWIN_A: core._audio_pin_metadata("usb-Weird-00")}


def test_declined_substitute_warns_that_edge_core_will_hold_the_twin(monkeypatch, tmp_path):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=BRIO)
    node = _node(tmp_path, "video0")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node})
    _patch_camera_helpers(monkeypatch, core, [_FakeCam(0, "C920", node)], prompt_inputs=[])
    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: False)
    printed: list[str] = []
    monkeypatch.setattr(
        core.console, "print", lambda *a, **k: printed.append(str(a[0]) if a else "")
    )

    core._detect_and_select_cameras()

    assert any("edge-core will" in line for line in printed)


def test_inference_stops_early_when_nobody_can_be_decided(monkeypatch):
    """Twelve interchangeable twins and devices must not enumerate 12! assignments."""
    import time

    core = load_core_module(monkeypatch)
    started = time.monotonic()
    result = core._infer_associations(
        [f"t{i}" for i in range(12)], list(range(12)), claimed=set(), compatible=_any
    )
    assert result.assigned == {}
    assert time.monotonic() - started < 1.0


def test_udev_pin_name_rejects_non_udev_values_by_construction(monkeypatch):
    core = load_core_module(monkeypatch)
    directory = core._V4L_BY_ID_DIR
    assert core._udev_pin_name(C920, directory) == C920
    assert core._udev_pin_name(f"{directory}/{C920}", directory) == C920
    assert core._udev_pin_name(SERIAL_A, directory) is None  # a raw serial
    assert core._udev_pin_name("video0", directory) is None
    assert core._udev_pin_name("../usb-x", directory) is None


def test_keeping_a_by_id_serial_pin_queues_its_migration_only_when_used(monkeypatch, tmp_path):
    """Resolving a pin has no side effect; keeping it is what moves it to video_device."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
    node0, node1 = _node(tmp_path, "video0"), _node(tmp_path, "video1")
    _tree(monkeypatch, core, tmp_path, "_V4L_BY_ID_DIR", {C920: node0, BRIO: node1})
    _write_twin_json(tmp_path, TWIN_A, "Cam A", camera=True, serial=C920)
    cams = [_FakeCam(0, "C920", node0), _FakeCam(1, "Brio", node1)]
    pinned: dict = {}

    # Declined: the old value is dropped from view, so nothing is migrated for it;
    # only the operator's fresh answer is written.
    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=[])
    monkeypatch.setattr(core.Confirm, "ask", lambda *a, **k: False)
    monkeypatch.setattr(core, "_prompt_camera_index", lambda *a, **k: 1)
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)
    core._detect_and_select_cameras()
    assert pinned == {TWIN_A: core._camera_pin_metadata(BRIO)}

    # Kept: the same twin is migrated to video_device without moving cameras.
    pinned.clear()
    _patch_camera_helpers(monkeypatch, core, cams, prompt_inputs=[])
    monkeypatch.setattr(core, "_persist_twin_pins", lambda p: pinned.update(p) or 0)
    core._detect_and_select_cameras()
    assert pinned == {TWIN_A: core._camera_pin_metadata(C920)}
