"""Regression tests for ``RateTracker``.

Pins the fix for the 20–40 msgs/s aliasing observed on a steady 30 fps
``data/frames/color_camera`` stream while running ``cyberwave worker
monitor``.  The worker publishes a stats snapshot every ~1.4s, the monitor
refreshes every ~2.0s; the two periods are not phase-locked, so a naive
``(delta_count) / (monitor_wall_clock_elapsed)`` divisor swings the
displayed rate even when the underlying source is constant.

The fix anchors the divisor on the worker-side snapshot timestamp and
de-duplicates identical snapshots so the dashboard only updates on fresh
data.
"""

from __future__ import annotations

import pytest

from cyberwave_cli.monitor import RateTracker, _display_channel


def _transport(channel: str, count: int, byte_count: int = 0) -> dict:
    """Build a minimal ``transport`` dict mirroring what the worker emits."""
    return {
        "publish": {channel: count},
        "recv": {},
        "publish_bytes": {channel: byte_count},
        "recv_bytes": {},
    }


class TestSnapshotClockAnchoring:
    """Rate must be computed against the snapshot timestamp, not wall-clock."""

    def test_first_call_seeds_and_returns_zero_rate(self):
        tracker = RateTracker()
        results = tracker.update(_transport("frames/color_camera", 0), snapshot_ts=0.0)
        assert len(results) == 1
        assert results[0].msgs_per_sec == 0.0
        assert results[0].total == 0

    def test_steady_30fps_source_reports_30_msgs_per_sec(self):
        """A counter advancing by 30 in 1.0 snapshot-second = 30 msgs/s."""
        tracker = RateTracker()
        tracker.update(_transport("frames/color_camera", 0), snapshot_ts=0.0)
        results = tracker.update(_transport("frames/color_camera", 30), snapshot_ts=1.0)
        assert results[0].msgs_per_sec == pytest.approx(30.0)

    def test_aliased_2s_monitor_over_1_4s_stats_publish_does_not_swing(self):
        """The user's observed scenario.

        Worker publishes stats every 1.4s with a true 30 fps source.
        Monitor refreshes every 2.0s.  Pre-fix, dividing the counter delta
        captured between two monitor ticks by ``2.0`` yielded an apparent
        rate that swung between ~21 and ~42 msgs/s.  Post-fix, the divisor
        is the snapshot ts delta and stale snapshots return cached values,
        so the displayed rate stays at a steady 30 msgs/s.
        """
        tracker = RateTracker()
        # t=0.0: first stats snapshot (counter=0).  Monitor tick aligns.
        tracker.update(_transport("frames", 0), snapshot_ts=0.0)

        # t=2.0: monitor tick.  The latest snapshot the monitor has seen
        # over Zenoh was published at t=1.4 with counter=42 (1.4s * 30fps).
        r1 = tracker.update(_transport("frames", 42), snapshot_ts=1.4)
        assert r1[0].msgs_per_sec == pytest.approx(30.0)

        # t=4.0: monitor tick.  Two more stats snapshots arrived since:
        # at t=2.8 (counter=84) and t=4.2 (counter=126).  The monitor
        # always sees the latest, so transport.publish=126, ts=4.2.
        r2 = tracker.update(_transport("frames", 126), snapshot_ts=4.2)
        assert r2[0].msgs_per_sec == pytest.approx(30.0)

        # t=6.0: monitor tick.  No new stats snapshot has arrived yet
        # (next worker publish is at t=5.6 → counter=168, but suppose it
        # was queued and the monitor's latest copy is still ts=4.2).
        # Pre-fix this would compute 0/2.0 = 0 and the dashboard would
        # flicker to zero.  Post-fix the cached rate is returned.
        r3 = tracker.update(_transport("frames", 126), snapshot_ts=4.2)
        assert r3[0].msgs_per_sec == pytest.approx(30.0)

        # t=8.0: fresh snapshot at t=5.6 with counter=168.
        r4 = tracker.update(_transport("frames", 168), snapshot_ts=5.6)
        assert r4[0].msgs_per_sec == pytest.approx(30.0)

    def test_stale_snapshot_returns_previous_results_object(self):
        tracker = RateTracker()
        tracker.update(_transport("frames", 0), snapshot_ts=10.0)
        first = tracker.update(_transport("frames", 60), snapshot_ts=12.0)
        stale = tracker.update(_transport("frames", 60), snapshot_ts=12.0)
        # Same snapshot ts ⇒ same cached output, regardless of refresh count.
        assert stale == first


class TestBackwardCompatibility:
    """Calling ``update`` without ``snapshot_ts`` falls back to wall-clock."""

    def test_legacy_call_signature_still_works(self):
        tracker = RateTracker()
        # Pre-fix call style used in any external embedders.
        seeded = tracker.update(_transport("frames", 0))
        rolled = tracker.update(_transport("frames", 5))
        assert seeded[0].msgs_per_sec == 0.0
        # Wall-clock is non-deterministic but the rate must be non-negative
        # and finite (a smoke test, not a precise assertion).
        assert 0.0 <= rolled[0].msgs_per_sec < 1e6


class TestMultipleChannels:
    """Per-channel computation should be independent."""

    def test_two_channels_independent_rates(self):
        tracker = RateTracker()
        t0 = {
            "publish": {"frames": 0, "detections": 0},
            "recv": {},
            "publish_bytes": {},
            "recv_bytes": {},
        }
        t1 = {
            "publish": {"frames": 30, "detections": 5},
            "recv": {},
            "publish_bytes": {},
            "recv_bytes": {},
        }
        tracker.update(t0, snapshot_ts=0.0)
        results = {r.channel: r.msgs_per_sec for r in tracker.update(t1, snapshot_ts=1.0)}
        assert results["frames"] == pytest.approx(30.0)
        assert results["detections"] == pytest.approx(5.0)


class TestDisplayChannel:
    """``_display_channel`` must keep multi-twin rows visually distinct.

    Pins the fix for the monitor collapsing two distinct per-twin
    subscriptions (``cw/<twin_A>/data/frames/color_camera`` and
    ``cw/<twin_B>/data/frames/color_camera``) to the same display row,
    which made multi-twin workers look like only one camera was publishing.
    """

    def test_full_canonical_key_keeps_short_twin_prefix(self):
        """``cw/<uuid>/data/frames/<sensor>`` → ``<uuid8>/data/frames/<sensor>``."""
        key = "cw/c91fde0e-1234-5678-9abc-def012345678/data/frames/color_camera"
        assert _display_channel(key) == "c91fde0e/data/frames/color_camera"

    def test_two_twins_same_channel_render_distinct(self):
        """Two twins publishing under the same sensor name must not collapse.

        This is the exact user-visible bug: both generated hooks pin to
        ``color_camera`` (the shared asset sensor name) so both subscriptions
        only differ by their twin UUID.  The monitor used to strip the UUID
        entirely, making the two rows look like duplicates.
        """
        twin_a = "cw/aaaaaaaa-1111-2222-3333-444444444444/data/frames/color_camera"
        twin_b = "cw/bbbbbbbb-1111-2222-3333-444444444444/data/frames/color_camera"
        assert _display_channel(twin_a) != _display_channel(twin_b)
        assert _display_channel(twin_a) == "aaaaaaaa/data/frames/color_camera"
        assert _display_channel(twin_b) == "bbbbbbbb/data/frames/color_camera"

    def test_sensor_less_channel_preserved(self):
        """Channels without a sensor segment (``imu``, ``joint_states``) keep rendering."""
        key = "cw/c91fde0e-1234-5678-9abc-def012345678/data/joint_states"
        assert _display_channel(key) == "c91fde0e/data/joint_states"

    def test_already_short_key_returned_unchanged(self):
        """Short keys (already in display form) pass through untouched."""
        assert _display_channel("frames/color_camera") == "frames/color_camera"
        assert _display_channel("worker_stats") == "worker_stats"
