"""
Example worker for testing the `cyberwave worker` CLI lifecycle.

This file demonstrates a complete custom worker that can be installed,
listed, and removed via the CLI commands added in CYB-1548:

    # 1. Install this worker
    cyberwave worker add examples/worker_lifecycle_demo.py

    # 2. Confirm it appears in the list
    cyberwave worker list

    # 3. Check JSON output
    cyberwave worker list --json

    # 4. Inspect container status alongside worker files
    cyberwave worker status

    # 5. Remove it (without .py extension)
    cyberwave worker remove worker_lifecycle_demo --yes

What this worker does at runtime
---------------------------------
Subscribes to camera frames from every twin in the environment.
On each frame it runs a lightweight YOLOv8n model and publishes a
``person_detected`` event whenever a person is spotted with confidence >= 0.6.
A simple per-twin counter throttles events to at most one per second so the
event stream stays readable during manual testing.

Prerequisites (runtime only — not needed for CLI testing)
----------------------------------------------------------
- The worker is loaded by the worker container, not run directly.
- ``cw`` is injected as a builtin by the Cyberwave worker runtime.
- Model weights are fetched automatically from the model catalog by
  ``cyberwave-edge-core``'s ModelManager before the container starts.

For IDE type-checking support uncomment:
    from cyberwave import Cyberwave; cw: Cyberwave
"""

import time

# ---------------------------------------------------------------------------
# Module-level setup — runs once when the worker is loaded
# ---------------------------------------------------------------------------

# Load the model. Edge Core pre-downloads weights to the model cache before
# the container starts, so this resolves immediately from disk.
model = cw.models.load("yolov8n")  # type: ignore[name-defined]  # noqa: F821

# Read the list of twin UUIDs this worker should subscribe to.
# Edge Core injects CYBERWAVE_TWIN_UUIDS into the container environment.
twin_uuids: list[str] = cw.config.twin_uuids  # type: ignore[name-defined]  # noqa: F821

# Throttle: record the last time we published an event per twin.
_last_event_at: dict[str, float] = {}
_EVENT_COOLDOWN_SECONDS = 1.0


# ---------------------------------------------------------------------------
# Hook registrations — one per twin
# ---------------------------------------------------------------------------

def _make_handler(twin_uuid: str):  # type: ignore[return]  # noqa: F821
    """Return a frame handler bound to *twin_uuid*."""

    @cw.on_frame(twin_uuid, sensor="default")  # type: ignore[name-defined]  # noqa: F821
    def handle_frame(frame, ctx):
        results = model.predict(frame, classes=["person"], confidence=0.6)

        if not results:
            return

        now = time.monotonic()
        if now - _last_event_at.get(twin_uuid, 0.0) < _EVENT_COOLDOWN_SECONDS:
            return

        _last_event_at[twin_uuid] = now

        cw.publish_event(  # type: ignore[name-defined]  # noqa: F821
            twin_uuid,
            "person_detected",
            {
                "count": len(results),
                "max_confidence": max(d.confidence for d in results),
                "model": "yolov8n",
                "frame_ts": ctx.timestamp,
            },
        )

    return handle_frame


# Register one handler per configured twin.
for _uuid in twin_uuids:
    _make_handler(_uuid)
