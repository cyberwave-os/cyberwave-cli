"""Identical USB units: collision by udev ID_SERIAL, pins by USB port, one device as camera and mic.

Two units that share a udev ID_SERIAL (vendor, model and USB serial) cannot be told apart by
any by-id name. They are pinned through their by-path link instead, while every unit with a
distinct ID_SERIAL keeps its by-id pin. Cameras and audio follow the same rules, and a single
piece of hardware (a C920 is a camera and a microphone) is pinned once per sensor.
"""
from __future__ import annotations

import json
import os
import random
import subprocess
from types import SimpleNamespace

import pytest

from tests._core_module_loader import load_core_module, use_production_device_paths
from tests.test_camera_mapping import TWIN_A, TWIN_B, TWIN_C, _FakeCam, _write_twin_json
from tests.test_udev_pinning import _node, _tree, _write_audio_twin

TWIN_D = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"

C920_ID = "046d_HD_Pro_Webcam_C920"
C920_ID_A = f"{C920_ID}_8E3A1B2F"
C920_ID_B = f"{C920_ID}_77C0FFEE"
RS_SERIAL = "926223022293"
RS_ID = f"Intel_RealSense_Depth_Camera_435_{RS_SERIAL}"
UMA_ID = "miniDSP_UMA16v2_00026"

# What udev publishes for a C920 without a USB serial: both units claim the same names.
C920_VIDEO_BY_ID = "usb-046d_HD_Pro_Webcam_C920-video-index0"
C920_AUDIO_BY_ID = "usb-046d_HD_Pro_Webcam_C920-02"
RS_VIDEO_BY_ID = f"usb-Intel_RealSense_Depth_Camera_435_{RS_SERIAL}-video-index0"
UMA_AUDIO_BY_ID = "usb-miniDSP_UMA16v2_00026-00"
PORT_1, PORT_2, PORT_3 = (f"platform-xhci-hcd.{n}-usb-0:2:1" for n in (1, 0, 2))


class Rig:
    """A fake host: V4L and ALSA trees, udev ID_SERIALs, scripted prompts, recorded pins."""

    def __init__(self, monkeypatch, core, tmp_path):
        self.mp, self.core, self.tmp = monkeypatch, core, tmp_path
        self.serials: dict[str, str] = {}
        self.v4l_id: dict[str, str] = {}
        self.v4l_path: dict[str, str] = {}
        self.alsa_id: dict[str, str] = {}
        self.alsa_path: dict[str, str] = {}
        self.cams: list[_FakeCam] = []
        self.cards: list[dict] = []
        self.prompts: list[str] = []
        self.confirms: list[tuple[str, object]] = []
        self.printed: list[str] = []
        self.pinned: dict[str, dict] = {}
        self.persist_calls: list[dict] = []
        self.answers: list[str] = []
        self.confirm_answers: dict[str, bool] = {}
        self.confirm_default = True
        self.closed_stdin = False
        self.accept_defaults = False
        self.enter_for_confirms = False
        monkeypatch.setattr(core, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(core, "ENVIRONMENT_FILE", tmp_path / "environment.json")
        monkeypatch.setattr(core.platform, "system", lambda: "Linux")
        monkeypatch.setattr(core, "_udev_properties", self._udev_properties)
        monkeypatch.setattr(core, "_persist_twin_pins", self._persist)
        monkeypatch.setattr(core.Prompt, "ask", self._prompt)
        monkeypatch.setattr(core.Confirm, "ask", self._confirm)
        monkeypatch.setattr(
            core.console, "print", lambda *a, **k: self.printed.append(str(a[0]) if a else "")
        )
        import cyberwave_cli.device_utils as device_utils

        monkeypatch.setattr(device_utils, "discover_usb_cameras", lambda: self.cams)
        monkeypatch.setattr(device_utils, "camera_likelihood_score", lambda _cam: 80)
        monkeypatch.setattr(core, "_upload_cameras_to_edge_metadata", lambda _d: True)

    # ---- hardware -----------------------------------------------------------------

    def camera(self, index, card, *, id_serial, by_id=None, by_path=None, v4l_serial=None):
        node = _node(self.tmp, f"video{index}")
        if id_serial:
            self.serials[node] = id_serial
        if by_id:
            self.v4l_id[by_id] = node
        if by_path:
            self.v4l_path[by_path] = node
        cam = _FakeCam(index, card, node, serial=v4l_serial)
        self.cams.append(cam)
        return cam

    def audio(
        self, index, label, usbid, *, id_serial, by_id=None, by_path=None, capture=True,
        playback=False,
    ):
        node = _node(self.tmp, f"controlC{index}")
        if id_serial:
            self.serials[f"/dev/snd/controlC{index}"] = id_serial
        if by_id:
            self.alsa_id[by_id] = node
        if by_path:
            self.alsa_path[by_path] = node
        self.cards.append(
            {"index": index, "label": label, "usbid": usbid, "capture": capture,
             "playback": playback}
        )

    def build(self):
        core, mp = self.core, self.mp
        _tree(mp, core, self.tmp, "_V4L_BY_ID_DIR", self.v4l_id)
        _tree(mp, core, self.tmp, "_V4L_BY_PATH_DIR", self.v4l_path)
        _tree(mp, core, self.tmp, "_ALSA_BY_ID_DIR", self.alsa_id)
        _tree(mp, core, self.tmp, "_ALSA_BY_PATH_DIR", self.alsa_path)
        proc = self.tmp / "asound"
        proc.mkdir()
        for card in self.cards:
            d = proc / f"card{card['index']}"
            d.mkdir()
            (d / "usbid").write_text(card["usbid"] + "\n")
            (d / "id").write_text(card["label"] + "\n")
            if card["capture"]:
                (d / "pcm0c").mkdir()
            if card["playback"]:
                (d / "pcm0p").mkdir()
        mp.setattr(core, "_PROC_ASOUND_DIR", str(proc))
        return self

    def path(self, tree, name):
        return f"{getattr(self.core, tree)}/{name}"

    # ---- fakes ----------------------------------------------------------------------

    def _udev_properties(self, node):
        return {"ID_SERIAL": self.serials[node]} if node in self.serials else {}

    def _persist(self, pins):
        self.persist_calls.append({u: dict(m) for u, m in pins.items()})
        for uuid_, metadata in pins.items():
            self.pinned.setdefault(uuid_, {}).update(metadata)
        return len(pins)

    def _prompt(self, prompt, **_kw):
        self.prompts.append(prompt)
        if self.closed_stdin:
            raise EOFError
        if self.accept_defaults and not self.answers:
            return _kw["default"]
        return self.answers.pop(0)

    def _confirm(self, prompt, **kw):
        self.confirms.append((prompt, kw.get("default")))
        if self.closed_stdin:
            raise EOFError
        for needle, value in self.confirm_answers.items():
            if needle in prompt:
                return value
        return kw.get("default", True) if self.enter_for_confirms else self.confirm_default

    # ---- twins ------------------------------------------------------------------------

    def camera_twin(self, uuid_, name, **kw):
        _write_twin_json(self.tmp, uuid_, name, camera=True, **kw)

    def mic_twin(self, uuid_, name, **kw):
        _write_audio_twin(self.tmp, uuid_, name, **kw)

    def combo_twin(self, uuid_, name):
        """One twin carrying both a camera and a microphone sensor (a C920 as a single twin)."""
        sensors = [{"type": "camera", "id": "cam0"}, {"type": "microphone", "id": "mic0"}]
        payload = {"uuid": uuid_, "name": name, "asset": {"universal_schema": {"sensors": sensors}}}
        (self.tmp / f"{uuid_}.json").write_text(json.dumps(payload))

    # ---- flows ------------------------------------------------------------------------

    def cameras_json(self):
        return json.loads((self.tmp / "cameras.json").read_text())

    def run_cameras(self):
        self.core._detect_and_select_cameras()
        return self

    def run_audio(self):
        self.core._detect_and_select_audio_devices()
        return self


@pytest.fixture
def rig(monkeypatch, tmp_path):
    return Rig(monkeypatch, load_core_module(monkeypatch), tmp_path)


def pi_cameras(rig):
    """The reported host: a RealSense plus two identical C920s without a USB serial."""
    rs = rig.camera(2, "RealSense", id_serial=RS_ID, by_id=RS_VIDEO_BY_ID,
                    by_path=f"{PORT_3}.0-video-index0")
    # udev gave the shared names to different units: index0 to one, index1 to the other.
    a = rig.camera(0, "C920", id_serial=C920_ID, by_id=C920_VIDEO_BY_ID,
                   by_path=f"{PORT_1}.0-video-index0")
    b = rig.camera(9, "C920", id_serial=C920_ID,
                   by_id="usb-046d_HD_Pro_Webcam_C920-video-index1",
                   by_path=f"{PORT_2}.0-video-index0")
    return rs, a, b


def pi_audio(rig):
    """The reported host: the UMA-16 and the microphones of the same two C920s."""
    rig.audio(2, "UMA16v2", "2752:0016", id_serial=UMA_ID, by_id=UMA_AUDIO_BY_ID,
              by_path=f"{PORT_3}.1")
    rig.audio(3, "C920", "046d:08e5", id_serial=C920_ID, by_id=C920_AUDIO_BY_ID,
              by_path=f"{PORT_1}.2")
    rig.audio(4, "C920_1", "046d:08e5", id_serial=C920_ID, by_path=f"{PORT_2}.2")


# ---- helpers ---------------------------------------------------------------------------


def test_collision_is_decided_by_udev_id_serial_not_by_counting_links(monkeypatch):
    core = load_core_module(monkeypatch)
    assert core._collided(["a", "a", "b"], [("m", True)] * 3) == [True, True, False]
    assert core._collided(["a", "b"], [("m", True), ("m", False)]) == [False, False]
    assert core._collided(["a"], [("m", True)]) == [False]


def test_collision_falls_back_to_link_counting_only_for_units_without_udev_data(monkeypatch):
    core = load_core_module(monkeypatch)
    # No udev data at all: same model where some but not all units own a by-id link.
    assert core._collided([None, None], [("m", True), ("m", False)]) == [True, True]
    assert core._collided([None, None], [("m", True), ("m", True)]) == [False, False]
    assert core._collided([], []) == []
    mixed = [("m", True), ("m", False), ("n", True), ("n", False)]
    assert core._collided(["a", None, None, None], mixed) == [False, False, True, True]
    assert core._collided(["a", "a", None, None], mixed) == [True, True, True, True]


def test_usb_unit_prefers_by_id_and_uses_by_path_when_units_collide(monkeypatch):
    core = load_core_module(monkeypatch)
    trees = ("/dev/v4l/by-id", "/dev/v4l/by-path")
    unique = core._usb_unit("usb-X-video-index0", "pci-1-video-index0", False, trees)
    assert unique == core._UsbUnit("/dev/v4l/by-id/usb-X-video-index0", False, False)
    same = core._usb_unit("usb-X-video-index0", "pci-1-video-index0", True, trees)
    assert same == core._UsbUnit("/dev/v4l/by-path/pci-1-video-index0", True, True)
    assert core._usb_unit("usb-X-video-index0", None, True, trees).pin is None
    only_port = core._usb_unit(None, "pci-1-video-index0", False, trees)
    assert only_port.pin == "/dev/v4l/by-path/pci-1-video-index0" and only_port.by_port
    assert core._usb_unit(None, None, False, trees).pin is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/dev/snd/by-id/usb-A-00", "/dev/snd/by-id/usb-A-00"),
        (
            "/dev/snd/by-path/platform-xhci-hcd.1-usb-0:2:1.2",
            "/dev/snd/by-path/platform-xhci-hcd.1-usb-0:2:1.2",
        ),
        ("usb-A-00", "/dev/snd/by-id/usb-A-00"),
        (" usb-A-00 ", "/dev/snd/by-id/usb-A-00"),
        ("00026", None),
        ("/dev/snd/by-id/../controlC3", None),
        ("/dev/snd/by-path/x/y", None),
        ("/dev/video0", None),
        ("", None),
    ],
)
def test_udev_link_path_accepts_both_trees_and_rejects_serials(monkeypatch, value, expected):
    core = load_core_module(monkeypatch)
    use_production_device_paths(monkeypatch, core)
    assert core._udev_link_path(value, core._alsa_trees()) == expected


def test_pin_metadata_for_either_tree(monkeypatch):
    core = load_core_module(monkeypatch)
    use_production_device_paths(monkeypatch, core)
    port = "/dev/v4l/by-path/platform-xhci-hcd.1-usb-0:2:1.0-video-index0"
    assert core._camera_pin_metadata(port) == {"video_device": port, "serial_number": ""}
    assert core._camera_pin_metadata(RS_SERIAL) == {"serial_number": RS_SERIAL}
    audio = "/dev/snd/by-path/platform-xhci-hcd.1-usb-0:2:1.2"
    assert core._audio_pin_metadata(audio) == {"audio_device": audio, "serial_number": ""}
    assert core._audio_pin_metadata("usb-A-00")["audio_device"] == "/dev/snd/by-id/usb-A-00"


def test_udev_properties_parses_udevadm_and_survives_its_absence(monkeypatch):
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "_udev_properties", core._real_udev_properties)
    out = "DEVNAME=/dev/video0\nID_SERIAL=046d_HD_Pro_Webcam_C920\nID_PATH=pci-0\n"
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=out)
    )
    assert core._udev_properties("/dev/video0")["ID_SERIAL"] == "046d_HD_Pro_Webcam_C920"
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=1, stdout="")
    )
    assert core._udev_properties("/dev/video0") == {}

    def _missing(*_a, **_k):
        raise FileNotFoundError("udevadm")

    monkeypatch.setattr(subprocess, "run", _missing)
    assert core._udev_properties("/dev/video0") == {}


@pytest.mark.allow_host_commands
def test_udev_properties_runs_udevadm_with_the_expected_arguments(monkeypatch, tmp_path):
    """Exercise the real subprocess call against a stand-in udevadm on PATH."""
    core = load_core_module(monkeypatch)
    monkeypatch.setattr(core, "_udev_properties", core._real_udev_properties)
    script = tmp_path / "bin" / "udevadm"
    script.parent.mkdir()
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = info ] && [ "$2" = --query=property ] && [ "$3" = --name=/dev/video4 ]; then\n'
        "  echo ID_SERIAL=046d_HD_Pro_Webcam_C920; echo ID_PATH=platform-xhci-hcd.1-usb-0:2:1.0\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{script.parent}:/usr/bin:/bin")
    monkeypatch.setattr(core, "clean_subprocess_env", lambda: dict(os.environ))
    assert core._udev_properties("/dev/video4") == {
        "ID_SERIAL": "046d_HD_Pro_Webcam_C920",
        "ID_PATH": "platform-xhci-hcd.1-usb-0:2:1.0",
    }
    assert core._udev_properties("/dev/video5") == {}


# ---- cameras: what is offered and how it is pinned -----------------------------------------


def test_camera_units_use_by_id_for_unique_and_by_path_for_identical(rig):
    rs, a, b = pi_cameras(rig)
    rig.build()
    units = rig.core._camera_units([rs, a, b])
    assert units[0].pin == rig.path("_V4L_BY_ID_DIR", RS_VIDEO_BY_ID) and not units[0].collided
    for unit, port in ((units[1], PORT_1), (units[2], PORT_2)):
        assert unit.collided and unit.by_port
        assert unit.pin == rig.path("_V4L_BY_PATH_DIR", f"{port}.0-video-index0")


def test_the_second_c920_is_not_offered_a_by_id_name_that_can_flip(rig):
    """udev split the shared names across the units; ID_SERIAL still shows they are identical."""
    rs, a, b = pi_cameras(rig)
    rig.build()
    units = rig.core._camera_units([rs, a, b])
    assert units[2].pin.startswith(rig.core._V4L_BY_PATH_DIR)
    assert "video-index1" not in units[2].pin


def test_camera_menu_marks_identical_units_and_shows_the_port(rig):
    rs, a, b = pi_cameras(rig)
    rig.build()
    rig.core._render_camera_menu([rs, a, b])
    text = "\n".join(rig.printed)
    assert text.count(" *") == 2  # the two identical C920s
    assert text.count("identical units, pinned by USB port") == 1  # one footnote, not one per line
    assert "id 926223022293" in text  # the RealSense, by its USB serial
    assert f"port {PORT_1.removeprefix('platform-')}.0" in text
    assert f"port {PORT_2.removeprefix('platform-')}.0" in text
    assert "video-index" not in text and "/dev/" not in text


def test_two_identical_c920_cameras_are_pinned_by_port_not_by_name(rig):
    _rs, a, b = pi_cameras(rig)
    rig.build()
    rig.camera_twin(TWIN_A, "Camera A")
    rig.camera_twin(TWIN_B, "Camera B")
    rig.answers = ["0", "9"]

    rig.run_cameras()

    port_a = rig.path("_V4L_BY_PATH_DIR", f"{PORT_1}.0-video-index0")
    port_b = rig.path("_V4L_BY_PATH_DIR", f"{PORT_2}.0-video-index0")
    assert rig.pinned == {
        TWIN_A: {"video_device": port_a, "serial_number": ""},
        TWIN_B: {"video_device": port_b, "serial_number": ""},
    }
    assert rig.cameras_json()["twin_to_device"] == {TWIN_A: port_a, TWIN_B: port_b}


def test_identical_cameras_asked_once_then_the_last_follows_by_elimination(rig):
    for n in (0, 1, 2):
        rig.camera(n, "C920", id_serial=C920_ID, by_id=C920_VIDEO_BY_ID if n == 0 else None,
                   by_path=f"platform-p{n}-video-index0")
    rig.build()
    for uuid_, name in ((TWIN_A, "A"), (TWIN_B, "B"), (TWIN_C, "C")):
        rig.camera_twin(uuid_, name)
    rig.answers = ["1", "0"]

    rig.run_cameras()

    assert len(rig.prompts) == 2  # the third twin is the only camera left
    links = {u: m["video_device"].rsplit("/", 1)[1] for u, m in rig.pinned.items()}
    assert links == {
        TWIN_A: "platform-p1-video-index0",
        TWIN_B: "platform-p0-video-index0",
        TWIN_C: "platform-p2-video-index0",
    }


def test_realsense_stays_by_id_while_the_identical_c920s_go_by_port(rig):
    rs, a, b = pi_cameras(rig)
    rig.build()
    rig.camera_twin(TWIN_A, "RealSense", depth=True)
    rig.camera_twin(TWIN_B, "C920")
    rig.answers = ["2", "9"]

    rig.run_cameras()

    assert rig.pinned[TWIN_A]["video_device"] == rig.path("_V4L_BY_ID_DIR", RS_VIDEO_BY_ID)
    assert rig.pinned[TWIN_B]["video_device"] == rig.path(
        "_V4L_BY_PATH_DIR", f"{PORT_2}.0-video-index0"
    )


def test_a_lone_c920_is_pinned_by_id_even_though_a_port_link_exists(rig):
    rig.camera(0, "C920", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-video-index0",
               by_path=f"{PORT_1}.0-video-index0")
    rig.build()
    rig.camera_twin(TWIN_A, "Camera")

    rig.run_cameras()

    assert rig.pinned[TWIN_A]["video_device"] == rig.path(
        "_V4L_BY_ID_DIR", f"usb-{C920_ID_A}-video-index0"
    )
    assert not any("pinned by USB port" in line for line in rig.printed)


def test_two_c920_with_distinct_usb_serials_both_keep_their_by_id_pin(rig):
    for n, ident in ((0, C920_ID_A), (1, C920_ID_B)):
        rig.camera(n, "C920", id_serial=ident, by_id=f"usb-{ident}-video-index0",
                   by_path=f"{PORT_1 if n == 0 else PORT_2}.0-video-index0")
    rig.build()
    rig.camera_twin(TWIN_A, "A")
    rig.camera_twin(TWIN_B, "B")
    rig.answers = ["1", "0"]

    rig.run_cameras()

    assert rig.pinned[TWIN_A]["video_device"].endswith(f"usb-{C920_ID_B}-video-index0")
    assert rig.pinned[TWIN_B]["video_device"].endswith(f"usb-{C920_ID_A}-video-index0")
    assert all(
        m["video_device"].startswith(rig.core._V4L_BY_ID_DIR) for m in rig.pinned.values()
    )


def test_a_c920_with_a_serial_next_to_one_without_does_not_collide(rig):
    rig.camera(0, "C920", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-video-index0",
               by_path=f"{PORT_1}.0-video-index0")
    rig.camera(1, "C920", id_serial=C920_ID, by_id=C920_VIDEO_BY_ID,
               by_path=f"{PORT_2}.0-video-index0")
    rig.build()
    units = rig.core._camera_units(rig.cams)
    assert [u.collided for u in units] == [False, False]
    assert all(u.pin.startswith(rig.core._V4L_BY_ID_DIR) for u in units)


def test_identical_cameras_without_any_stable_link_cannot_be_pinned(rig):
    rig.camera(0, "C920", id_serial=C920_ID, by_id=C920_VIDEO_BY_ID)
    rig.camera(1, "C920", id_serial=C920_ID)
    rig.build()
    rig.camera_twin(TWIN_A, "Camera")
    rig.answers = ["1"]

    rig.run_cameras()

    assert rig.pinned == {}
    assert any("cannot be pinned" in line for line in rig.printed)
    assert rig.cameras_json()["twin_to_device"] == {TWIN_A: 1}  # still usable this run


def test_without_udev_data_the_link_count_rule_still_protects_identical_units(rig):
    rig.camera(0, "C920", id_serial=None, by_id=C920_VIDEO_BY_ID,
               by_path=f"{PORT_1}.0-video-index0")
    rig.camera(1, "C920", id_serial=None, by_path=f"{PORT_2}.0-video-index0")
    rig.build()
    units = rig.core._camera_units(rig.cams)
    assert [u.collided for u in units] == [True, True]
    assert all(u.pin.startswith(rig.core._V4L_BY_PATH_DIR) for u in units)


def test_a_camera_without_udev_data_does_not_disable_detection_for_the_usb_ones(rig):
    """A CSI camera has no ID_SERIAL; the two identical C920 next to it are still caught."""
    rig.camera(0, "imx219", id_serial=None, by_path="platform-csi-video-index0")
    rig.camera(1, "C920", id_serial=C920_ID, by_id=C920_VIDEO_BY_ID,
               by_path=f"{PORT_1}.0-video-index0")
    rig.camera(2, "C920", id_serial=C920_ID, by_path=f"{PORT_2}.0-video-index0")
    rig.build()
    units = rig.core._camera_units(rig.cams)
    assert [u.collided for u in units] == [False, True, True]
    assert units[0].pin == rig.path("_V4L_BY_PATH_DIR", "platform-csi-video-index0")
    assert units[1].pin.startswith(rig.core._V4L_BY_PATH_DIR)


# ---- cameras: reinstall -------------------------------------------------------------------


def test_closed_stdin_with_unpinned_identical_cameras_skips_instead_of_crashing(rig):
    pi_cameras(rig)
    rig.build()
    rig.camera_twin(TWIN_A, "Camera A")
    rig.closed_stdin = True

    rig.run_cameras()

    assert rig.pinned == {} and not (rig.tmp / "cameras.json").exists()
    assert any("No input available" in line for line in rig.printed)


def test_reinstall_with_by_path_pins_binds_each_twin_to_its_port_without_a_menu(rig):
    rs, a, b = pi_cameras(rig)
    rig.build()
    port_a = rig.path("_V4L_BY_PATH_DIR", f"{PORT_1}.0-video-index0")
    port_b = rig.path("_V4L_BY_PATH_DIR", f"{PORT_2}.0-video-index0")
    rig.camera_twin(TWIN_A, "Camera A", video_device=port_b)
    rig.camera_twin(TWIN_B, "Camera B", video_device=port_a)

    rig.run_cameras()

    assert rig.prompts == []
    assert [c[0] for c in rig.confirms] == ["Keep these existing camera bindings?"]
    assert rig.cameras_json()["twin_to_device"] == {TWIN_A: port_b, TWIN_B: port_a}
    assert rig.pinned == {}
    assert not any("device device" in line for line in rig.printed)
    assert any(line.startswith("[green]Using saved device[/green]") for line in rig.printed)


def test_a_saved_by_id_name_shared_by_identical_cameras_is_not_trusted(rig):
    """The reported host: the C920 twin said 'connected' to a name that can move after a reboot."""
    rs, a, b = pi_cameras(rig)
    rig.build()
    shared = rig.path("_V4L_BY_ID_DIR", C920_VIDEO_BY_ID)
    rig.camera_twin(TWIN_A, "Logitech C920", video_device=shared)
    rig.answers = ["0"]

    rig.run_cameras()

    assert any("shared by identical cameras" in line for line in rig.printed)
    assert rig.prompts, "the operator must choose again"
    assert rig.pinned[TWIN_A]["video_device"] == rig.path(
        "_V4L_BY_PATH_DIR", f"{PORT_1}.0-video-index0"
    )


def test_a_by_id_pin_on_a_unique_c920_is_still_trusted(rig):
    """Only identical units make a by-id name untrustworthy; a C920 next to a Brio is fine."""
    rig.camera(0, "C920", id_serial=C920_ID, by_id=C920_VIDEO_BY_ID,
               by_path=f"{PORT_1}.0-video-index0")
    rig.camera(1, "Brio", id_serial="046d_Brio", by_id="usb-046d_Brio-video-index0")
    rig.build()
    rig.camera_twin(TWIN_A, "Camera", video_device=rig.path("_V4L_BY_ID_DIR", C920_VIDEO_BY_ID))

    rig.run_cameras()

    assert rig.prompts == [] and rig.pinned == {}
    assert not any("name shared" in line for line in rig.printed)


def test_a_saved_by_path_pin_with_nothing_at_that_port_is_stale(rig):
    rig.camera(0, "C920", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-video-index0")
    rig.build()
    gone = rig.path("_V4L_BY_PATH_DIR", f"{PORT_2}.0-video-index0")
    rig.camera_twin(TWIN_A, "Camera", video_device=gone)
    rig.confirm_default = True

    rig.run_cameras()

    assert any("is not attached" in c[0] for c in rig.confirms)
    assert rig.pinned[TWIN_A]["video_device"].endswith(f"usb-{C920_ID_A}-video-index0")


def test_swapping_the_cameras_between_ports_keeps_the_binding_on_the_port(rig):
    """By-path is the USB port: the camera now at that port is the twin's camera."""
    rs, a, b = pi_cameras(rig)
    rig.build()
    port_a = rig.path("_V4L_BY_PATH_DIR", f"{PORT_1}.0-video-index0")
    rig.camera_twin(TWIN_A, "Camera", video_device=port_a)

    rig.run_cameras()

    assert rig.cameras_json()["twin_to_device"][TWIN_A] == port_a


# ---- audio: the same rules for microphones ------------------------------------------------


def test_audio_cards_of_identical_c920s_are_pinnable_by_port(rig):
    pi_audio(rig)
    rig.build()
    cards = {c.label: c for c in rig.core._discover_usb_audio_cards()}
    uma, c1, c2 = cards["UMA16v2"], cards["C920"], cards["C920_1"]
    assert not uma.unit.collided and uma.pin.endswith(UMA_AUDIO_BY_ID)
    for card, port in ((c1, PORT_1), (c2, PORT_2)):
        assert card.unit.collided
        assert card.pin == rig.path("_ALSA_BY_PATH_DIR", f"{port}.2")


def test_the_c920_microphones_are_offered_to_the_generic_microphone(rig):
    """The reported run: UMA-16 taken, both C920 microphones refused, 'Only one device' offered."""
    pi_audio(rig)
    rig.build()
    uma = rig.path("_ALSA_BY_ID_DIR", UMA_AUDIO_BY_ID)
    rig.mic_twin(TWIN_A, "UMA-16 v2", extra={"audio_device": uma})
    rig.mic_twin(TWIN_B, "Generic Microphone")
    rig.answers = ["4"]  # the menu is numbered by ALSA card: C920_1 is card 4

    rig.run_audio()

    assert rig.prompts == ["Audio device for Generic Microphone"]
    assert rig.pinned == {
        TWIN_B: {
            "audio_device": rig.path("_ALSA_BY_PATH_DIR", f"{PORT_2}.2"),
            "serial_number": "",
        }
    }
    assert not any("Only one device" in c[0] for c in rig.confirms)


def test_closed_stdin_never_binds_the_microphone_to_a_device_another_twin_uses(rig):
    rig.audio(2, "UMA16v2", "2752:0016", id_serial=UMA_ID, by_id=UMA_AUDIO_BY_ID)
    rig.build()
    uma = rig.path("_ALSA_BY_ID_DIR", UMA_AUDIO_BY_ID)
    rig.mic_twin(TWIN_A, "UMA-16 v2", extra={"audio_device": uma})
    rig.mic_twin(TWIN_B, "Generic Microphone")
    rig.closed_stdin = True

    rig.run_audio()

    assert rig.pinned == {}


def test_a_microphone_used_by_another_twin_is_never_offered_again(rig):
    rig.audio(2, "UMA16v2", "2752:0016", id_serial=UMA_ID, by_id=UMA_AUDIO_BY_ID)
    rig.build()
    uma = rig.path("_ALSA_BY_ID_DIR", UMA_AUDIO_BY_ID)
    rig.mic_twin(TWIN_A, "UMA-16 v2", extra={"audio_device": uma})
    rig.mic_twin(TWIN_B, "Generic Microphone")
    rig.confirm_default = True

    rig.run_audio()

    assert rig.pinned == {}
    assert rig.prompts == [] and not any("Use it" in c[0] for c in rig.confirms)
    assert any(
        "No free audio device is left for Generic Microphone" in line for line in rig.printed
    )


def test_two_microphone_twins_on_two_identical_c920s_are_pinned_by_port(rig):
    rig.audio(3, "C920", "046d:08e5", id_serial=C920_ID, by_id=C920_AUDIO_BY_ID,
              by_path=f"{PORT_1}.2")
    rig.audio(4, "C920_1", "046d:08e5", id_serial=C920_ID, by_path=f"{PORT_2}.2")
    rig.build()
    rig.mic_twin(TWIN_A, "Mic A")
    rig.mic_twin(TWIN_B, "Mic B")
    rig.answers = ["4"]

    rig.run_audio()

    assert rig.prompts == ["Audio device for Mic A"]  # Mic B is the one left
    assert rig.pinned[TWIN_A]["audio_device"].endswith(f"{PORT_2}.2")
    assert rig.pinned[TWIN_B]["audio_device"].endswith(f"{PORT_1}.2")
    assert all(m["serial_number"] == "" for m in rig.pinned.values())


def test_a_lone_c920_microphone_is_pinned_by_id(rig):
    rig.audio(3, "C920", "046d:08e5", id_serial=C920_ID_A,
              by_id=f"usb-{C920_ID_A}-02", by_path=f"{PORT_1}.2")
    rig.build()
    rig.mic_twin(TWIN_A, "Mic")

    rig.run_audio()

    assert rig.pinned[TWIN_A]["audio_device"] == rig.path("_ALSA_BY_ID_DIR", f"usb-{C920_ID_A}-02")
    assert rig.prompts == [] and not any("pinned by USB port" in line for line in rig.printed)


def test_two_c920_microphones_with_distinct_serials_keep_their_by_id_pin(rig):
    rig.audio(3, "C920", "046d:08e5", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-02",
              by_path=f"{PORT_1}.2")
    rig.audio(4, "C920_1", "046d:08e5", id_serial=C920_ID_B, by_id=f"usb-{C920_ID_B}-02",
              by_path=f"{PORT_2}.2")
    rig.build()
    assert [c.unit.collided for c in rig.core._discover_usb_audio_cards()] == [False, False]
    assert all(
        c.pin.startswith(rig.core._ALSA_BY_ID_DIR) for c in rig.core._discover_usb_audio_cards()
    )


def test_identical_microphones_without_any_stable_link_cannot_be_pinned(rig):
    rig.audio(3, "C920", "046d:08e5", id_serial=C920_ID, by_id=C920_AUDIO_BY_ID)
    rig.audio(4, "C920_1", "046d:08e5", id_serial=C920_ID)
    rig.build()
    rig.mic_twin(TWIN_A, "Mic")

    rig.run_audio()

    assert rig.pinned == {}
    assert any("cannot be pinned" in line for line in rig.printed)


def test_a_speaker_twin_never_gets_the_capture_only_c920(rig):
    pi_audio(rig)
    rig.audio(5, "Boombox", "1234:5678", id_serial="Acme_Boombox_55",
              by_id="usb-Acme_Boombox_55-00", capture=False, playback=True)
    rig.build()
    rig.mic_twin(TWIN_A, "Speaker", sensor="speaker")

    rig.run_audio()

    assert rig.pinned[TWIN_A]["audio_device"].endswith("usb-Acme_Boombox_55-00")
    assert rig.prompts == []


def test_a_saved_by_id_microphone_name_shared_by_identical_units_is_not_trusted(rig):
    pi_audio(rig)
    rig.build()
    shared = rig.path("_ALSA_BY_ID_DIR", C920_AUDIO_BY_ID)
    rig.mic_twin(TWIN_A, "Mic", extra={"audio_device": shared})
    rig.answers = ["3"]  # the pool is card 2 (UMA-16), 3 (C920), 4 (C920_1)

    rig.run_audio()

    assert any("name identical units share" in line for line in rig.printed)
    assert rig.pinned[TWIN_A]["audio_device"] == rig.path("_ALSA_BY_PATH_DIR", f"{PORT_1}.2")


def test_a_saved_by_path_microphone_pin_is_reused_without_a_question(rig):
    pi_audio(rig)
    rig.build()
    port = rig.path("_ALSA_BY_PATH_DIR", f"{PORT_2}.2")
    rig.mic_twin(TWIN_A, "Mic", extra={"audio_device": port})

    rig.run_audio()

    assert rig.prompts == []
    assert [c[0] for c in rig.confirms] == ["Keep these existing audio bindings?"]
    assert rig.pinned == {}  # already in the new shape


def test_a_saved_by_path_microphone_pin_with_nothing_at_that_port_is_stale(rig):
    rig.audio(3, "C920", "046d:08e5", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-02")
    rig.build()
    gone = rig.path("_ALSA_BY_PATH_DIR", f"{PORT_2}.2")
    rig.mic_twin(TWIN_A, "Mic", extra={"audio_device": gone})

    rig.run_audio()

    assert any("is not connected" in c[0] for c in rig.confirms)
    assert rig.pinned[TWIN_A]["audio_device"].endswith(f"usb-{C920_ID_A}-02")


def test_a_by_path_pin_with_a_leftover_serial_is_rewritten_clean(rig):
    pi_audio(rig)
    rig.build()
    port = rig.path("_ALSA_BY_PATH_DIR", f"{PORT_1}.2")
    rig.mic_twin(TWIN_A, "Mic", serial=port.rsplit("/", 1)[1], extra={"audio_device": port})

    rig.run_audio()

    assert rig.pinned[TWIN_A] == {"audio_device": port, "serial_number": ""}


# ---- one piece of hardware, two sensors, two drivers ---------------------------------------


def two_c920_hardware(rig):
    """Two identical C920s, each a camera and a microphone, with no USB serial."""
    rig.camera(0, "C920", id_serial=C920_ID, by_id=C920_VIDEO_BY_ID,
               by_path=f"{PORT_1}.0-video-index0")
    rig.camera(9, "C920", id_serial=C920_ID, by_path=f"{PORT_2}.0-video-index0")
    rig.audio(3, "C920", "046d:08e5", id_serial=C920_ID, by_id=C920_AUDIO_BY_ID,
              by_path=f"{PORT_1}.2")
    rig.audio(4, "C920_1", "046d:08e5", id_serial=C920_ID, by_path=f"{PORT_2}.2")
    rig.build()


def test_a_c920_is_pinned_as_a_camera_and_as_a_microphone_by_two_drivers(rig):
    two_c920_hardware(rig)
    rig.camera_twin(TWIN_A, "Generic Camera")
    rig.mic_twin(TWIN_B, "Generic Microphone")
    rig.answers = ["9", "4"]

    rig.run_cameras().run_audio()

    assert rig.pinned[TWIN_A] == {
        "video_device": rig.path("_V4L_BY_PATH_DIR", f"{PORT_2}.0-video-index0"),
        "serial_number": "",
    }
    assert rig.pinned[TWIN_B] == {
        "audio_device": rig.path("_ALSA_BY_PATH_DIR", f"{PORT_2}.2"),
        "serial_number": "",
    }


def test_one_twin_with_both_sensors_gets_both_pins_without_clobbering_either(rig):
    two_c920_hardware(rig)
    rig.combo_twin(TWIN_A, "C920 twin")
    rig.answers = ["9", "4"]

    rig.run_cameras().run_audio()

    assert rig.pinned[TWIN_A] == {
        "video_device": rig.path("_V4L_BY_PATH_DIR", f"{PORT_2}.0-video-index0"),
        "serial_number": "",
        "audio_device": rig.path("_ALSA_BY_PATH_DIR", f"{PORT_2}.2"),
    }
    assert len(rig.persist_calls) == 2  # one write per sensor flow, merged on the backend


def test_camera_and_microphone_of_the_same_hardware_do_not_have_to_match(rig):
    """The operator may drive the camera from one C920 and the microphone from the other."""
    two_c920_hardware(rig)
    rig.camera_twin(TWIN_A, "Generic Camera")
    rig.mic_twin(TWIN_B, "Generic Microphone")
    rig.answers = ["0", "4"]

    rig.run_cameras().run_audio()

    assert rig.pinned[TWIN_A]["video_device"].endswith(f"{PORT_1}.0-video-index0")
    assert rig.pinned[TWIN_B]["audio_device"].endswith(f"{PORT_2}.2")


def test_the_reported_installation_end_to_end(rig):
    """Four twins on the Pi: the camera twins and the microphone twins, reinstall included."""
    rs, a, b = pi_cameras(rig)
    pi_audio(rig)
    rig.build()
    rig.camera_twin(TWIN_A, "Realsense D455", depth=True)
    rig.camera_twin(TWIN_B, "Logitech C920")
    rig.mic_twin(TWIN_C, "UMA-16 v2")
    rig.mic_twin(TWIN_D, "Generic Microphone")
    rig.answers = ["2", "9", "2", "4"]  # D455, C920 at port 2, UMA-16 (card 2), C920 mic (card 4)

    rig.run_cameras().run_audio()

    assert rig.pinned[TWIN_A]["video_device"].endswith(RS_VIDEO_BY_ID)
    assert rig.pinned[TWIN_B]["video_device"].endswith(f"{PORT_2}.0-video-index0")
    assert rig.pinned[TWIN_C]["audio_device"].endswith(UMA_AUDIO_BY_ID)
    assert rig.pinned[TWIN_D]["audio_device"].endswith(f"{PORT_2}.2")

    for uuid_, metadata in rig.pinned.items():
        path = rig.tmp / f"{uuid_}.json"
        data = json.loads(path.read_text())
        data["metadata"] = metadata
        path.write_text(json.dumps(data))
    rig.pinned.clear()
    rig.prompts.clear()
    rig.confirms.clear()
    rig.answers = []

    rig.run_cameras().run_audio()

    assert rig.prompts == []
    assert rig.pinned == {}
    assert {c[0] for c in rig.confirms} == {
        "Keep these existing camera bindings?",
        "Keep these existing audio bindings?",
    }
    assert not any("name shared" in line or "name identical" in line for line in rig.printed)


# ---- every combination of unique and identical units ---------------------------------------

LAYOUTS = {
    "one-unique": ["a"],
    "two-unique": ["a", "b"],
    "two-identical": ["g", "g"],
    "three-identical": ["g", "g", "g"],
    "unique-and-pair": ["a", "g", "g"],
    "pair-and-unique": ["g", "g", "a"],
    "two-pairs": ["g", "g", "h", "h"],
    "unique-pair-pair": ["a", "g", "g", "h", "h"],
    "pair-of-pairs-and-uniques": ["g", "a", "g", "b", "h", "h"],
}


def _twin_uuid(n):
    return f"{n:08x}-0000-4000-8000-000000000000"


def _build_cameras(rig, labels):
    seen: set[str] = set()
    for i, label in enumerate(labels):
        first = label not in seen
        seen.add(label)
        rig.camera(
            10 + i, f"Model-{label}", id_serial=f"ID_{label}",
            by_id=f"usb-ID_{label}-video-index0" if first else None,
            by_path=f"pci-port{i}-video-index0",
        )


def _build_audio(rig, labels):
    seen: set[str] = set()
    for i, label in enumerate(labels):
        first = label not in seen
        seen.add(label)
        rig.audio(
            10 + i, f"Card-{label}-{i}", f"0000:{label}", id_serial=f"ID_{label}",
            by_id=f"usb-ID_{label}-00" if first else None, by_path=f"pci-port{i}.2",
        )


def _expected_tree(labels, link_name_prefix_by_id):
    """Per unit: True when identical units share its udev id (so it must be pinned by port)."""
    return [labels.count(label) > 1 for label in labels]


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_camera_layout_pins_unique_by_id_and_identical_by_port(rig, layout):
    labels = LAYOUTS[layout]
    collided = _expected_tree(labels, None)
    _build_cameras(rig, labels)
    rig.build()
    for n in range(1, len(labels) + 1):
        rig.pinned.clear()
        rig.prompts.clear()
        rig.confirms.clear()
        for stale in rig.tmp.glob("*-0000-4000-8000-*.json"):
            stale.unlink()
        rig.accept_defaults = True
        for t in range(n):
            rig.camera_twin(_twin_uuid(t), f"T{t}")

        rig.run_cameras()

        pins = {u: m["video_device"] for u, m in rig.pinned.items()}
        if len(labels) == 1:
            assert set(pins) == {_twin_uuid(t) for t in range(n)} if n == 1 else True
        if len(labels) > 1:
            assert len(pins) == n, (layout, n, rig.printed)
            assert len(set(pins.values())) == n, "two twins pinned to one camera"
        for twin, pin in pins.items():
            unit = rig.core._camera_units(rig.cams)[
                next(i for i, c in enumerate(rig.cams) if pin.rsplit("/", 1)[1].startswith(
                    (f"usb-ID_{labels[i]}-", f"pci-port{i}-")))
            ]
            assert unit.pin == pin
            tree = rig.core._V4L_BY_PATH_DIR if unit.collided else rig.core._V4L_BY_ID_DIR
            assert pin.startswith(tree + "/"), (layout, n, pin)
        assert all(m["serial_number"] == "" for m in rig.pinned.values())
        if len(labels) >= 2:
            assert len(rig.prompts) <= max(n, 0)
            assert len(rig.prompts) >= n - 1 if n == len(labels) else True
        assert [u.collided for u in rig.core._camera_units(rig.cams)] == collided


@pytest.mark.parametrize("layout", LAYOUTS)
def test_every_microphone_layout_pins_unique_by_id_and_identical_by_port(rig, layout):
    labels = LAYOUTS[layout]
    collided = _expected_tree(labels, None)
    _build_audio(rig, labels)
    rig.build()
    for n in range(1, len(labels) + 1):
        rig.pinned.clear()
        rig.prompts.clear()
        rig.confirms.clear()
        for stale in rig.tmp.glob("*-0000-4000-8000-*.json"):
            stale.unlink()
        rig.accept_defaults = True
        for t in range(n):
            rig.mic_twin(_twin_uuid(t), f"T{t}")

        rig.run_audio()

        pins = {u: m["audio_device"] for u, m in rig.pinned.items()}
        assert len(pins) == n, (layout, n, rig.printed)
        assert len(set(pins.values())) == n, "two twins pinned to one microphone"
        cards = rig.core._discover_usb_audio_cards()
        for pin in pins.values():
            card = next(c for c in cards if c.pin == pin)
            tree = rig.core._ALSA_BY_PATH_DIR if card.unit.collided else rig.core._ALSA_BY_ID_DIR
            assert pin.startswith(tree + "/"), (layout, n, pin)
        assert all(m["serial_number"] == "" for m in rig.pinned.values())
        if n == len(labels):
            assert len(rig.prompts) == n - 1  # the last twin is the one device left
        assert [c.unit.collided for c in cards] == collided


# ---- twins versus physical sensors -----------------------------------------------------------


def _identical_hardware(rig, units, *, cameras=True, audio=True):
    """*units* identical C920s without a USB serial, each a camera and a microphone."""
    for i in range(units):
        if cameras:
            rig.camera(10 + i, "C920", id_serial=C920_ID,
                       by_id=C920_VIDEO_BY_ID if i == 0 else None,
                       by_path=f"pci-port{i}-video-index0")
        if audio:
            rig.audio(20 + i, f"C920_{i}", "046d:08e5", id_serial=C920_ID,
                      by_id=C920_AUDIO_BY_ID if i == 0 else None, by_path=f"pci-port{i}.2")
    rig.build()
    rig.accept_defaults = True
    rig.enter_for_confirms = True  # Enter at every prompt, as an operator who just accepts


def _count(rig, needle):
    return sum(needle in line for line in rig.printed)


@pytest.mark.parametrize("units", [1, 2, 3, 4])
@pytest.mark.parametrize("twins", [1, 2, 3, 4])
def test_cameras_one_sensor_serves_one_twin_and_the_mismatch_is_warned(rig, units, twins):
    _identical_hardware(rig, units, audio=False)
    for t in range(twins):
        rig.camera_twin(_twin_uuid(t), f"Cam{t}")

    rig.run_cameras()

    pins = [m["video_device"] for m in rig.pinned.values()]
    assert len(pins) == min(units, twins)
    assert len(set(pins)) == len(pins), "a camera was given to two twins"
    listing = "\n".join(rig.printed)
    for i in range(units):
        assert f"port port{i}" in listing or units == 1
    assert _count(rig, "have no physical camera") == (1 if twins > units else 0)
    assert _count(rig, "are not used by any twin") == (1 if units > twins else 0)
    if twins > units:
        assert f"{twins - units} camera twin(s) have no physical camera" in listing
    if units > twins:
        assert f"{units - twins} camera(s) are not used by any twin" in listing
    mapped = rig.cameras_json()["twin_to_device"]
    assert len(mapped) == min(units, twins)


@pytest.mark.parametrize("units", [1, 2, 3, 4])
@pytest.mark.parametrize("twins", [1, 2, 3, 4])
def test_microphones_one_sensor_serves_one_twin_and_the_mismatch_is_warned(rig, units, twins):
    _identical_hardware(rig, units, cameras=False)
    for t in range(twins):
        rig.mic_twin(_twin_uuid(t), f"Mic{t}")

    rig.run_audio()

    pins = [m["audio_device"] for m in rig.pinned.values()]
    assert len(pins) == min(units, twins)
    assert len(set(pins)) == len(pins), "a microphone was given to two twins"
    listing = "\n".join(rig.printed)
    assert f"Detected {units} USB audio device(s)" in listing
    for card in rig.core._discover_usb_audio_cards():
        assert card.label in listing
    assert _count(rig, "have no physical device") == (1 if twins > units else 0)
    assert _count(rig, "are not used by any twin") == (1 if units > twins else 0)
    if twins > units:
        assert f"{twins - units} audio twin(s) have no physical device" in listing
    if units > twins:
        assert f"{units - twins} audio device(s) are not used by any twin" in listing


@pytest.mark.parametrize("units", [1, 2, 3])
@pytest.mark.parametrize(
    ("cam_twins", "mic_twins"), [(1, 1), (2, 2), (3, 1), (1, 3), (0, 2), (2, 0)]
)
def test_each_c920_is_a_camera_and_a_microphone_pinned_separately(
    rig, units, cam_twins, mic_twins
):
    _identical_hardware(rig, units)
    for t in range(cam_twins):
        rig.camera_twin(_twin_uuid(t), f"Cam{t}")
    for t in range(mic_twins):
        rig.mic_twin(_twin_uuid(100 + t), f"Mic{t}")

    rig.run_cameras().run_audio()

    cams = [m["video_device"] for m in rig.pinned.values() if "video_device" in m]
    mics = [m["audio_device"] for m in rig.pinned.values() if "audio_device" in m]
    assert len(cams) == (min(units, cam_twins) if cam_twins else 0)
    assert len(mics) == min(units, mic_twins)
    assert len(set(cams)) == len(cams) and len(set(mics)) == len(mics)
    for line in rig.printed:
        assert not ("camera twin" in line and "audio" in line)
    assert _count(rig, "camera twin(s) have no physical camera") == int(cam_twins > units)
    assert _count(rig, "audio twin(s) have no physical device") == int(mic_twins > units)


def test_the_operator_decides_which_twin_gets_the_only_camera(rig):
    _identical_hardware(rig, 1, audio=False)
    for t in range(3):
        rig.camera_twin(_twin_uuid(t), f"Cam{t}")
    rig.accept_defaults = False
    rig.answers = ["s", "s", "10"]  # the third twin gets it

    rig.run_cameras()

    assert set(rig.pinned) == {_twin_uuid(2)}
    assert "2 camera twin(s) have no physical camera: Cam0, Cam1" in "\n".join(rig.printed)


def test_the_operator_decides_which_twin_gets_the_only_microphone(rig):
    _identical_hardware(rig, 1, cameras=False)
    for t in range(3):
        rig.mic_twin(_twin_uuid(t), f"Mic{t}")
    rig.accept_defaults = False
    rig.confirm_answers = {"Only one device": False}
    answers = iter([False, False, True])
    rig.mp.setattr(
        rig.core.Confirm, "ask",
        lambda prompt, **kw: next(answers) if "Only one device" in prompt else False,
    )

    rig.run_audio()

    assert set(rig.pinned) == {_twin_uuid(2)}
    assert "2 audio twin(s) have no physical device: Mic0, Mic1" in "\n".join(rig.printed)


def test_a_camera_is_never_shared_even_when_the_operator_insists(rig):
    _identical_hardware(rig, 1, audio=False)
    for t in range(2):
        rig.camera_twin(_twin_uuid(t), f"Cam{t}")
    rig.accept_defaults = False
    rig.answers = ["10"]
    rig.confirm_default = True

    rig.run_cameras()

    assert set(rig.pinned) == {_twin_uuid(0)}
    assert rig.prompts == ["Camera for Cam0"]  # Cam1 is not even offered a taken camera
    assert not any("already used" in c[0] for c in rig.confirms)
    assert any("No free camera is left for Cam1" in line for line in rig.printed)


def test_typing_the_index_of_a_taken_camera_is_refused_and_asked_again(rig):
    _identical_hardware(rig, 3, audio=False)
    for t in range(3):
        rig.camera_twin(_twin_uuid(t), f"Cam{t}")
    rig.accept_defaults = False
    rig.answers = ["10", "10", "11"]  # the second twin first tries camera 10 again

    rig.run_cameras()

    assert rig.prompts == ["Camera for Cam0", "Camera for Cam1", "Camera for Cam1"]
    assert any("already bound to Cam0" in line for line in rig.printed)
    assert rig.pinned[_twin_uuid(1)]["video_device"].endswith("pci-port1-video-index0")


def test_an_earlier_twin_cannot_take_the_camera_a_later_twin_is_pinned_to(rig):
    _identical_hardware(rig, 3, audio=False)
    rig.camera_twin(_twin_uuid(0), "Cam0")
    rig.camera_twin(
        _twin_uuid(1), "Cam1", video_device=rig.path("_V4L_BY_PATH_DIR", "pci-port0-video-index0")
    )
    rig.accept_defaults = False
    rig.answers = ["10", "11"]  # Cam0 first tries the camera Cam1 owns

    rig.run_cameras()

    assert any("already bound to Cam1" in line for line in rig.printed)
    assert rig.pinned[_twin_uuid(0)]["video_device"].endswith("pci-port1-video-index0")


def test_two_camera_twins_saved_on_the_same_camera_keep_it_for_the_first_only(rig):
    _identical_hardware(rig, 2, audio=False)
    port0 = rig.path("_V4L_BY_PATH_DIR", "pci-port0-video-index0")
    rig.camera_twin(_twin_uuid(0), "Cam0", video_device=port0)
    rig.camera_twin(_twin_uuid(1), "Cam1", video_device=port0)
    rig.accept_defaults = True

    rig.run_cameras()

    assert any("already bound to Cam0" in line for line in rig.printed)
    mapped = rig.cameras_json()["twin_to_device"]
    assert mapped[_twin_uuid(0)] == port0
    assert mapped[_twin_uuid(1)] != port0  # it was moved to the free camera
    assert rig.pinned[_twin_uuid(1)]["video_device"].endswith("pci-port1-video-index0")


def test_a_duplicate_camera_pin_is_cleared_when_no_other_camera_is_free(rig):
    _identical_hardware(rig, 1, audio=False)
    port0 = rig.path("_V4L_BY_PATH_DIR", "pci-port0-video-index0")
    rig.camera_twin(_twin_uuid(0), "Cam0", video_device=port0)
    rig.camera_twin(_twin_uuid(1), "Cam1", video_device=port0)

    rig.run_cameras()

    assert rig.pinned[_twin_uuid(1)] == {"serial_number": "", "video_device": ""}
    assert _twin_uuid(1) not in rig.cameras_json()["twin_to_device"]
    assert "1 camera twin(s) have no physical camera: Cam1" in "\n".join(rig.printed)


def test_two_microphone_twins_saved_on_the_same_device_keep_it_for_the_first_only(rig):
    _identical_hardware(rig, 2, cameras=False)
    port0 = rig.path("_ALSA_BY_PATH_DIR", "pci-port0.2")
    rig.mic_twin(_twin_uuid(0), "Mic0", extra={"audio_device": port0})
    rig.mic_twin(_twin_uuid(1), "Mic1", extra={"audio_device": port0})

    rig.run_audio()

    assert any("already bound to Mic0" in line for line in rig.printed)
    assert _twin_uuid(0) not in rig.pinned  # the first twin's pin is already current
    assert rig.pinned[_twin_uuid(1)]["audio_device"].endswith("pci-port1.2")


def test_a_duplicate_microphone_pin_is_cleared_when_no_other_device_is_free(rig):
    _identical_hardware(rig, 1, cameras=False)
    path = rig.path("_ALSA_BY_ID_DIR", C920_AUDIO_BY_ID)
    rig.mic_twin(_twin_uuid(0), "Mic0", extra={"audio_device": path})
    rig.mic_twin(_twin_uuid(1), "Mic1", extra={"audio_device": path})

    rig.run_audio()

    assert rig.pinned[_twin_uuid(1)] == {"audio_device": "", "serial_number": ""}
    assert "1 audio twin(s) have no physical device: Mic1" in "\n".join(rig.printed)


def test_two_twins_typed_with_the_same_serial_do_not_both_bind_the_device(rig):
    rig.audio(3, "C920", "046d:08e5", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-02")
    rig.build()
    rig.mic_twin(_twin_uuid(0), "Mic0", serial="8E3A1B2F")
    rig.mic_twin(_twin_uuid(1), "Mic1", serial="8E3A1B2F")

    rig.run_audio()

    assert any("already bound to Mic0" in line for line in rig.printed)
    assert rig.pinned == {}  # the operator's own serials are never rewritten


def test_closed_stdin_leaves_the_surplus_twins_without_a_sensor(rig):
    _identical_hardware(rig, 1)
    rig.camera_twin(_twin_uuid(0), "Cam0")
    rig.camera_twin(_twin_uuid(1), "Cam1")
    rig.mic_twin(_twin_uuid(100), "Mic0")
    rig.mic_twin(_twin_uuid(101), "Mic1")
    rig.accept_defaults = False
    rig.answers = []
    rig.closed_stdin = True

    rig.run_cameras().run_audio()

    assert rig.pinned == {}


def test_every_attached_sensor_is_printed_even_when_they_are_the_same_hardware(rig):
    _identical_hardware(rig, 3)
    rig.camera_twin(_twin_uuid(0), "Cam0")
    rig.mic_twin(_twin_uuid(100), "Mic0")

    rig.run_cameras().run_audio()

    text = "\n".join(rig.printed)
    assert "Detected 3 camera(s) and 1 camera twin(s)" in text
    assert "Detected 3 USB audio device(s)" in text
    for i in range(3):
        assert f"port port{i}" in text
        assert f"port{i}.2" in text


# ---- more situations ---------------------------------------------------------------------------


def test_microphone_twins_without_any_usb_audio_device_get_a_clear_message(rig):
    rig.build()
    rig.mic_twin(_twin_uuid(0), "Mic0")

    rig.run_audio()

    assert rig.pinned == {}
    assert rig.prompts == []
    assert any("have no physical device" in line for line in rig.printed) or any(
        "No USB audio device" in line for line in rig.printed
    )


def test_camera_twins_with_cameras_missing_ask_nothing_and_map_nothing(rig):
    rig.build()
    rig.camera_twin(_twin_uuid(0), "Cam0")

    rig.run_cameras()

    assert rig.prompts == [] and rig.pinned == {}
    assert any("No cameras detected" in line for line in rig.printed)


def test_a_playback_only_device_is_not_reported_unused_when_only_microphones_exist(rig):
    rig.audio(3, "C920", "046d:08e5", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-02")
    rig.audio(5, "Boombox", "1234:5678", id_serial="Acme_Boombox_55",
              by_id="usb-Acme_Boombox_55-00", capture=False, playback=True)
    rig.build()
    rig.mic_twin(_twin_uuid(0), "Mic0")

    rig.run_audio()

    assert not any("not used by any twin" in line for line in rig.printed)


def test_garbage_at_the_camera_menu_leaves_the_remaining_twins_without_a_camera(rig):
    _identical_hardware(rig, 3, audio=False)
    for t in range(3):
        rig.camera_twin(_twin_uuid(t), f"Cam{t}")
    rig.accept_defaults = False
    rig.answers = ["10", "banana"]

    rig.run_cameras()

    assert set(rig.pinned) == {_twin_uuid(0)}
    assert "2 camera twin(s) have no physical camera: Cam1, Cam2" in "\n".join(rig.printed)


def test_skipping_every_camera_twin_maps_nothing(rig):
    _identical_hardware(rig, 2, audio=False)
    for t in range(2):
        rig.camera_twin(_twin_uuid(t), f"Cam{t}")
    rig.accept_defaults = False
    rig.answers = ["s", "s"]

    rig.run_cameras()

    assert rig.pinned == {} and not (rig.tmp / "cameras.json").exists()
    assert any("No cameras mapped" in line for line in rig.printed)


def test_reinstall_with_every_twin_pinned_has_nothing_to_warn_about(rig):
    _identical_hardware(rig, 2)
    for t in range(2):
        port = rig.path("_V4L_BY_PATH_DIR", f"pci-port{t}-video-index0")
        rig.camera_twin(_twin_uuid(t), f"Cam{t}", video_device=port)
        rig.mic_twin(
            _twin_uuid(100 + t), f"Mic{t}",
            extra={"audio_device": rig.path("_ALSA_BY_PATH_DIR", f"pci-port{t}.2")},
        )

    rig.run_cameras().run_audio()

    assert rig.prompts == [] and rig.pinned == {}
    for needle in ("have no physical", "not used by any twin", "left without"):
        assert not any(needle in line for line in rig.printed)


def test_reinstall_with_one_surplus_twin_warns_only_about_that_twin(rig):
    _identical_hardware(rig, 2)
    for t in range(2):
        port = rig.path("_V4L_BY_PATH_DIR", f"pci-port{t}-video-index0")
        rig.camera_twin(_twin_uuid(t), f"Cam{t}", video_device=port)
    rig.camera_twin(_twin_uuid(2), "Cam2")
    rig.accept_defaults = False
    rig.answers = ["s"]  # the extra twin is offered the two taken cameras and skips

    rig.run_cameras()

    text = "\n".join(rig.printed)
    assert "1 camera twin(s) have no physical camera: Cam2" in text
    assert "not used by any twin" not in text


def test_an_unplugged_identical_camera_leaves_its_twin_on_its_saved_port_pin(rig):
    _identical_hardware(rig, 1, audio=False)  # only the first of two C920s is attached
    port0 = rig.path("_V4L_BY_PATH_DIR", "pci-port0-video-index0")
    port1 = rig.path("_V4L_BY_PATH_DIR", "pci-port1-video-index0")
    rig.camera_twin(_twin_uuid(0), "Cam0", video_device=port0)
    rig.camera_twin(_twin_uuid(1), "Cam1", video_device=port1)  # that camera is unplugged
    rig.accept_defaults = False
    rig.answers = ["s"]

    rig.run_cameras()

    assert rig.cameras_json()["twin_to_device"] == {_twin_uuid(0): port0, _twin_uuid(1): port1}
    assert "keeps its saved camera pin" in "\n".join(rig.printed)
    assert rig.pinned == {}


# ---- a sensor serves exactly one twin, whatever the starting point --------------------------


def _random_labels(r):
    return [r.choice("ghab") for _ in range(r.randint(1, 4))]


@pytest.mark.parametrize("seed", range(80))
def test_no_camera_ends_up_bound_to_two_twins(rig, seed):
    r = random.Random(seed)
    labels = _random_labels(r)
    _build_cameras(rig, labels)
    rig.build()
    rig.accept_defaults = True
    rig.enter_for_confirms = True
    for t in range(r.randint(1, 5)):
        pin = r.choice(["none", "unit", "gone"])
        port = r.randrange(len(labels)) if pin == "unit" else 9
        device = rig.path("_V4L_BY_PATH_DIR", f"pci-port{port}-video-index0")
        extra = {} if pin == "none" else {"video_device": device}
        rig.camera_twin(_twin_uuid(t), f"Cam{t}", **extra)

    rig.run_cameras()

    if not (rig.tmp / "cameras.json").exists():
        return
    nodes = []
    for value in rig.cameras_json()["twin_to_device"].values():
        if isinstance(value, int):
            node = next(c.primary_path for c in rig.cams if c.index == value)
        elif isinstance(value, str) and value.startswith("/"):
            node = os.path.realpath(value)
        else:
            continue
        if os.path.exists(node):
            nodes.append(node)
    assert len(nodes) == len(set(nodes)), (seed, labels, rig.printed)


@pytest.mark.parametrize("seed", range(80))
def test_no_microphone_ends_up_bound_to_two_twins(rig, seed):
    r = random.Random(seed)
    labels = _random_labels(r)
    _build_audio(rig, labels)
    rig.build()
    rig.accept_defaults = True
    rig.enter_for_confirms = True
    original: dict[str, str] = {}
    for t in range(r.randint(1, 5)):
        pin = r.choice(["none", "unit", "gone"])
        port = r.randrange(len(labels)) if pin == "unit" else 9
        path = rig.path("_ALSA_BY_PATH_DIR", f"pci-port{port}.2")
        if pin == "none":
            rig.mic_twin(_twin_uuid(t), f"Mic{t}")
        else:
            rig.mic_twin(_twin_uuid(t), f"Mic{t}", extra={"audio_device": path})
            original[_twin_uuid(t)] = path

    rig.run_audio()

    final = dict(original)
    for uuid_, metadata in rig.pinned.items():
        if metadata.get("audio_device"):
            final[uuid_] = metadata["audio_device"]
        else:
            final.pop(uuid_, None)
    nodes = [os.path.realpath(p) for p in final.values() if os.path.exists(os.path.realpath(p))]
    assert len(nodes) == len(set(nodes)), (seed, labels, rig.printed)


# ---- menus: defaults that fit the twin, and honest wording ---------------------------------


def _record_defaults(rig, answer="default"):
    seen: list[tuple[str, int | None]] = []

    def prompt(_cameras, valid, *, prompt, default_index):
        seen.append((prompt, default_index))
        return default_index if default_index is not None else next(iter(valid))

    rig.mp.setattr(rig.core, "_prompt_camera_index", prompt)
    return seen


def test_a_depth_twin_defaults_to_the_realsense_and_a_c920_twin_to_a_c920(rig):
    rig.camera(8, "C920", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-video-index0")
    rig.camera(0, "C920", id_serial=C920_ID_B, by_id=f"usb-{C920_ID_B}-video-index0")
    rig.camera(2, "Intel RealSense D455", id_serial=RS_ID, by_id=RS_VIDEO_BY_ID)
    rig.build()
    rig.camera_twin(TWIN_A, "Realsense D455", depth=True)
    rig.camera_twin(TWIN_B, "Logitech C920")
    seen = _record_defaults(rig)

    rig.run_cameras()

    assert seen == [("Camera for Realsense D455", 2), ("Camera for Logitech C920", 8)]


def test_the_default_follows_the_twins_model_not_the_discovery_order(rig):
    rig.camera(1, "Brio 100", id_serial="046d_Brio", by_id="usb-046d_Brio-video-index0")
    rig.camera(5, "HD Pro Webcam C920", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-video-index0")
    rig.camera(6, "Other", id_serial="x_Other", by_id="usb-x_Other-video-index0")
    rig.build()
    rig.camera_twin(TWIN_A, "Camera")
    path = rig.tmp / f"{TWIN_A}.json"
    data = json.loads(path.read_text())
    data["asset"]["name"] = "Logitech C920"
    path.write_text(json.dumps(data))
    seen = _record_defaults(rig)

    rig.run_cameras()

    assert seen == [("Camera for Camera", 5)]


def test_with_no_fitting_camera_there_is_no_default(rig):
    rig.camera(1, "C920", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-video-index0")
    rig.camera(2, "C920", id_serial=C920_ID_B, by_id=f"usb-{C920_ID_B}-video-index0")
    rig.build()
    rig.camera_twin(TWIN_A, "Realsense", depth=True)  # depth twin, no depth camera attached
    seen = _record_defaults(rig)

    rig.run_cameras()

    assert seen == [("Camera for Realsense", None)]


def test_enter_without_a_default_asks_again_instead_of_choosing(rig):
    rig.camera(1, "C920", id_serial=C920_ID_A, by_id=f"usb-{C920_ID_A}-video-index0")
    rig.camera(2, "C920", id_serial=C920_ID_B, by_id=f"usb-{C920_ID_B}-video-index0")
    rig.build()
    answers = iter(["", "  ", "2"])
    asked: list[dict] = []

    def ask(prompt, **kwargs):
        asked.append(kwargs)
        return next(answers)

    rig.mp.setattr(rig.core.Prompt, "ask", ask)

    picked = rig.core._prompt_camera_index(
        rig.cams, {1: rig.cams[0], 2: rig.cams[1]}, prompt="Camera", default_index=None
    )

    assert picked == 2
    assert len(asked) == 3 and all("default" not in kw for kw in asked)
    assert any("Choose a camera number" in line for line in rig.printed)


def test_a_shared_by_id_pin_is_listed_with_its_reason_not_as_not_attached(rig):
    pi_cameras(rig)
    rig.build()
    shared = rig.path("_V4L_BY_ID_DIR", C920_VIDEO_BY_ID)
    rig.camera_twin(TWIN_A, "Logitech C920", video_device=shared)
    rig.answers = ["0"]

    rig.run_cameras()

    listed = [line for line in rig.printed if "Logitech C920:" in line]
    assert any("shared by identical cameras" in line and "choose again" in line for line in listed)
    assert not any("pinned camera not attached" in line for line in listed)


def test_a_truly_missing_camera_is_still_listed_as_not_attached(rig):
    pi_cameras(rig)
    rig.build()
    gone = rig.path("_V4L_BY_PATH_DIR", "platform-gone.0-video-index0")
    rig.camera_twin(TWIN_A, "Logitech C920", video_device=gone)
    rig.answers = ["0"]

    rig.run_cameras()

    assert any(
        "Logitech C920:" in line and "pinned camera not attached" in line for line in rig.printed
    )


def test_audio_menu_numbers_follow_the_alsa_card_and_stay_the_same_between_twins(rig):
    pi_audio(rig)
    rig.build()
    rig.mic_twin(TWIN_A, "Mic A")
    rig.mic_twin(TWIN_B, "Mic B")
    rig.answers = ["3", "4"]

    rig.run_audio()

    listed = [line for line in rig.printed if line.startswith(("  2)", "  3)", "  4)"))]
    assert len(listed) == 3  # every card, numbered by its ALSA card, printed once
    assert any("free: 2, 3, 4" in line for line in rig.printed)
    assert any("free: 2, 4" in line for line in rig.printed)  # same numbers for the next twin
    assert rig.pinned[TWIN_A]["audio_device"].endswith(f"{PORT_1}.2")


def test_a_bad_audio_answer_is_asked_again_with_the_valid_choices(rig):
    pi_audio(rig)
    rig.build()
    rig.mic_twin(TWIN_A, "Mic A")
    rig.answers = ["7", "zero", "4"]

    rig.run_audio()

    assert rig.prompts == ["Audio device for Mic A"] * 3
    assert any("choose 2, 3, 4 or s" in line for line in rig.printed)
    assert rig.pinned[TWIN_A]["audio_device"].endswith(f"{PORT_2}.2")


def test_closed_stdin_at_the_audio_menu_skips_the_remaining_twins(rig):
    pi_audio(rig)
    rig.build()
    rig.mic_twin(TWIN_A, "Mic A")
    rig.mic_twin(TWIN_B, "Mic B")
    rig.closed_stdin = True

    rig.run_audio()

    assert rig.pinned == {}
    assert rig.prompts == ["Audio device for Mic A"]  # the second twin is never asked
