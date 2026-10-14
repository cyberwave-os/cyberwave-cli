"""The camera picker must show what tells two cameras apart.

Two cameras of the same model render an identical ``card``. If the menu's only
other column is ``/dev/videoN``, the operator is choosing on the one attribute
that is explicitly *not* identity -- it is positional and moves on replug and
reboot. The serial is both the real discriminator and the value that goes into
``metadata.serial_number``, so it belongs on screen.
"""

from __future__ import annotations

import re
from typing import Any

from tests._core_module_loader import load_core_module


class _FakeCam:
    def __init__(
        self,
        idx: int,
        card: str,
        path: str,
        serial: str | None = None,
        bus_info: str | None = None,
        driver: str = "uvcvideo",
    ) -> None:
        self.index = idx
        self.card = card
        self.primary_path = path
        self.paths = [path]
        self.serial = serial
        # ``camera_likelihood_score`` reads both, and a low score switches the
        # renderer to its "probably not a camera" branch -- which these tests
        # are not about.
        # ``None`` means "unspecified, give me a plausible one"; ``""`` is a
        # deliberate absence and must survive.
        self.bus_info = "usb-xhci-hcd.0-1" if bus_info is None else bus_info
        self.driver = driver


class _RecordingConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args: Any, **_kwargs: Any) -> None:
        self.lines.append(" ".join(str(a) for a in args))


def _render(monkeypatch, cameras, **kwargs):
    core = load_core_module(monkeypatch)
    recorder = _RecordingConsole()
    monkeypatch.setattr(core, "console", recorder)
    indices = core._render_camera_menu(cameras, **kwargs)
    return core, recorder.lines, indices


def test_serial_is_shown_for_each_camera(monkeypatch):
    """The regression: identical models were indistinguishable in the menu."""
    cameras = [
        _FakeCam(0, "Intel(R) RealSense(TM) Depth Ca", "/dev/video0", serial="214523026669"),
        _FakeCam(6, "Intel(R) RealSense(TM) Depth Ca", "/dev/video6", serial="926223022293"),
    ]

    _, lines, _ = _render(monkeypatch, cameras)

    assert any("serial 214523026669" in line for line in lines)
    assert any("serial 926223022293" in line for line in lines)


def _descriptive(line: str) -> str:
    """The part of a menu row that is *not* the menu index or the device path.

    Both of those differ between any two rows by construction, so comparing
    whole lines would pass even with no identifying information at all -- the
    exact false negative this test exists to avoid.
    """
    plain = re.sub(r"\[/?[^\]]*\]", "", line)   # rich markup
    plain = re.sub(r"^\s*\d+\)\s*", "", plain)   # menu index
    plain = re.sub(r"/dev/video\d+", "", plain)   # positional path
    return plain.strip()


def test_two_identical_cameras_render_distinguishable_lines(monkeypatch):
    """Stronger than 'the serial appears': the rows must actually differ.

    Same model, so the ``card`` strings are identical. With the index and the
    device path removed, anything left must still tell them apart -- otherwise
    the operator is choosing on position.
    """
    cameras = [
        _FakeCam(0, "Intel(R) RealSense(TM) Depth Ca", "/dev/video0", serial="214523026669"),
        _FakeCam(6, "Intel(R) RealSense(TM) Depth Ca", "/dev/video6", serial="926223022293"),
    ]

    _, lines, _ = _render(monkeypatch, cameras)

    assert _descriptive(lines[0]) != _descriptive(lines[1])


def test_serialless_camera_falls_back_to_its_port(monkeypatch):
    """A C920 ships an empty serial descriptor; the port is all that is left.

    It is also the hint that this twin needs a ``video_device`` pin rather
    than a ``serial_number`` one.
    """
    cameras = [_FakeCam(14, "HD Pro Webcam C920", "/dev/video14", bus_info="usb-xhci-hcd.0-2")]

    _, lines, _ = _render(monkeypatch, cameras)

    assert "port usb-xhci-hcd.0-2" in lines[0]
    assert "serial" not in lines[0]


def test_camera_with_neither_serial_nor_port_still_renders(monkeypatch):
    """No hint is better than a crash or a dangling separator."""
    cameras = [_FakeCam(3, "Some Camera", "/dev/video3", bus_info="")]

    _, lines, _ = _render(monkeypatch, cameras)

    assert "video3" in lines[0]
    assert "serial" not in lines[0]
    assert "port " not in lines[0]


def test_assignment_annotation_is_preserved(monkeypatch):
    """The hint must not displace the existing 'assigned to' suffix."""
    cameras = [_FakeCam(6, "Cam", "/dev/video6", serial="ABC123")]

    _, lines, _ = _render(monkeypatch, cameras, assignments={6: "Twin A"})

    assert "serial ABC123" in lines[0]
    assert "assigned to Twin A" in lines[0]


def test_returned_index_map_is_unchanged(monkeypatch):
    """Rendering is cosmetic -- the caller's index contract must not move."""
    cameras = [
        _FakeCam(0, "A", "/dev/video0", serial="S0"),
        _FakeCam(6, "B", "/dev/video6"),
    ]

    _, _, indices = _render(monkeypatch, cameras)

    assert set(indices) == {0, 6}
    assert indices[0] is cameras[0]
    assert indices[6] is cameras[1]


def test_identity_hint_prefers_serial_over_port(monkeypatch):
    """When both exist the serial wins: it is portable, the port is not."""
    core = load_core_module(monkeypatch)
    cam = _FakeCam(0, "Cam", "/dev/video0", serial="S1", bus_info="usb-xhci-hcd.0-1")

    assert core._camera_identity_hint(cam) == "serial S1"


def test_identity_hint_ignores_blank_values(monkeypatch):
    """``v4l2-ctl`` prints an empty ``Serial :`` line for a C920."""
    core = load_core_module(monkeypatch)

    blank = _FakeCam(0, "Cam", "/dev/video0", serial="   ", bus_info="")
    assert core._camera_identity_hint(blank) == ""
    assert (
        core._camera_identity_hint(
            _FakeCam(0, "Cam", "/dev/video0", serial="  ", bus_info="usb-1")
        )
        == "port usb-1"
    )
