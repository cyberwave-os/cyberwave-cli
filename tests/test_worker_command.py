"""Tests for ``cyberwave worker add`` and ``cyberwave worker doctor``.

These cover the fixes for the edge-worker silent failure modes documented in
``docs-mintlify/edge/workers/overview.mdx``:

* ``worker add`` always writes files world-readable so the container user
  (UID 1001) can actually load them, and tolerates filesystems that can't
  honor chmod.
* ``worker doctor`` / the ``worker start`` pre-flight:
    - hard-fails only when the worker *definitely* cannot run (edge-core
      missing, docker missing, worker files not world-readable);
    - downgrades "no driver running" to a warning so pre-flight doesn't
      block legitimate startup orders or remote-router topologies;
    - compares env across driver and worker *containers*, not against the
      CLI host shell.
"""

from __future__ import annotations

import contextlib
import importlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner


def _module():
    # ``cyberwave_cli.commands.__init__`` re-exports the click Group under
    # the name ``worker``, which shadows attribute lookup on the parent
    # package. Use importlib to reach the actual module object.
    return importlib.import_module("cyberwave_cli.commands.worker")


# ---------------------------------------------------------------------------
# worker add
# ---------------------------------------------------------------------------


def test_worker_add_chmods_0644() -> None:
    """Copied worker files are world-readable regardless of source mode."""
    wm = _module()
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmp:
        workers_dir = Path(tmp) / "workers"
        src_fd, src_path = tempfile.mkstemp(suffix=".py")
        os.close(src_fd)
        src = Path(src_path)
        src.write_text("import cw\n")
        src.chmod(0o600)
        try:
            with patch.object(wm, "WORKERS_DIR", workers_dir):
                result = runner.invoke(wm.worker, ["add", str(src), "--force"])
            assert result.exit_code == 0, result.output
            dest = workers_dir / src.name
            assert dest.exists()
            assert dest.stat().st_mode & 0o777 == 0o644
        finally:
            src.unlink(missing_ok=True)


def test_worker_add_tolerates_chmod_failure() -> None:
    """If chmod raises (e.g. FUSE mount), warn but keep the copied file."""
    wm = _module()
    runner = CliRunner()

    real_chmod = Path.chmod

    def _chmod(self: Path, mode: int) -> None:
        # Only reject the destination's post-copy chmod; let the temporary
        # source file's chmod in this test succeed.
        if self.suffix == ".py" and self.parent.name == "workers":
            raise PermissionError("read-only fs")
        real_chmod(self, mode)

    with tempfile.TemporaryDirectory() as tmp:
        workers_dir = Path(tmp) / "workers"
        src_fd, src_path = tempfile.mkstemp(suffix=".py")
        os.close(src_fd)
        src = Path(src_path)
        src.write_text("import cw\n")
        try:
            with (
                patch.object(wm, "WORKERS_DIR", workers_dir),
                patch.object(Path, "chmod", _chmod),
            ):
                result = runner.invoke(wm.worker, ["add", str(src), "--force"])
            assert result.exit_code == 0, result.output
            assert "could not chmod" in result.output.lower()
            # The copy must still be present despite the chmod failure.
            assert (workers_dir / src.name).exists()
        finally:
            src.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# pre-flight / doctor: core checks
# ---------------------------------------------------------------------------


def _apply_docker_patches(
    stack: contextlib.ExitStack,
    wm,
    *,
    drivers=None,
    workers=None,
    envs=None,
) -> None:
    """Enter the docker-touching helper patches into *stack* so
    ``_collect_preflight_checks`` behaves deterministically without a
    real Docker daemon."""
    drivers = drivers or []
    workers = workers or []
    envs = envs or {}

    def _inspect(name: str) -> dict[str, str]:
        return envs.get(name, {})

    stack.enter_context(patch.object(wm.shutil, "which", return_value="/usr/bin/docker"))
    stack.enter_context(
        patch.object(wm, "_running_driver_containers", return_value=list(drivers))
    )
    stack.enter_context(
        patch.object(wm, "_running_worker_containers", return_value=list(workers))
    )
    stack.enter_context(patch.object(wm, "_inspect_container_env", side_effect=_inspect))


def test_preflight_flags_unreadable_worker_files() -> None:
    """0600 worker files must produce a blocking ``worker-perms`` check."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        f = wd / "secret.py"
        f.write_text("import cw\n")
        f.chmod(0o600)

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(wm, "_find_edge_core_binary", return_value="/usr/bin/cwec")
            )
            _apply_docker_patches(stack, wm, drivers=["cyberwave-driver-abc"])
            checks = wm._collect_preflight_checks(wd)
        named = {c.name: c for c in checks}
        assert "worker-perms" in named
        assert named["worker-perms"].level == "error"


def test_preflight_warns_when_no_worker_files() -> None:
    """Empty workers dir warns but does not block."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(wm, "_find_edge_core_binary", return_value="/usr/bin/cwec")
            )
            _apply_docker_patches(stack, wm)
            checks = wm._collect_preflight_checks(wd)
        named = {c.name: c for c in checks}
        assert named.get("worker-files") is not None
        assert named["worker-files"].level == "warn"


def test_preflight_driver_absent_is_warn_not_error() -> None:
    """No driver running must be a *warning* — edge-core may bring one up,
    or the worker may bind to a remote Zenoh router."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text("import cw\n")
        (wd / "w.py").chmod(0o644)
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(wm, "_find_edge_core_binary", return_value="/usr/bin/cwec")
            )
            _apply_docker_patches(stack, wm, drivers=[])
            checks = wm._collect_preflight_checks(wd)
        named = {c.name: c for c in checks}
        assert named["driver-container"].level == "warn"
        # No error-level checks — `worker start` must not abort here.
        assert not any(c.level == "error" for c in checks)


def test_preflight_docker_missing_is_error() -> None:
    """Without docker, edge-core can't run the worker at all — block."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text("import cw\n")
        (wd / "w.py").chmod(0o644)
        with (
            patch.object(wm, "_find_edge_core_binary", return_value="/usr/bin/cwec"),
            patch.object(wm.shutil, "which", return_value=None),
        ):
            checks = wm._collect_preflight_checks(wd)
        named = {c.name: c for c in checks}
        assert named["docker"].level == "error"


# ---------------------------------------------------------------------------
# pre-flight / doctor: env-consistency
# ---------------------------------------------------------------------------


def test_env_consistency_flags_driver_disagreement() -> None:
    """Two drivers on the same host with different CYBERWAVE_ENVIRONMENT
    values must raise an env-consistency warning."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text("import cw\n")
        (wd / "w.py").chmod(0o644)
        envs = {
            "cyberwave-driver-a": {
                "CYBERWAVE_ENVIRONMENT": "dev",
                "ZENOH_CONNECT": "tcp/router:7447",
            },
            "cyberwave-driver-b": {
                "CYBERWAVE_ENVIRONMENT": "prod",
                "ZENOH_CONNECT": "tcp/router:7447",
            },
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(wm, "_find_edge_core_binary", return_value="/usr/bin/cwec")
            )
            _apply_docker_patches(stack, wm, drivers=list(envs), envs=envs)
            checks = wm._collect_preflight_checks(wd)
        named = {c.name: c for c in checks}
        assert named["env-consistency"].level == "warn"
        assert "driver disagreement" in (named["env-consistency"].hint or "")


def test_env_consistency_ignores_host_shell_env() -> None:
    """Regression: the check must not compare against the CLI host's shell
    env. A wildly mismatched host env with consistent containers is fine."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text("import cw\n")
        (wd / "w.py").chmod(0o644)
        envs = {
            "cyberwave-driver-a": {
                "CYBERWAVE_ENVIRONMENT": "prod",
                "ZENOH_CONNECT": "tcp/router:7447",
                "CYBERWAVE_TWIN_UUID": "t1",
            },
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(wm, "_find_edge_core_binary", return_value="/usr/bin/cwec")
            )
            _apply_docker_patches(stack, wm, drivers=list(envs), envs=envs)
            # Deliberately set a disagreeing shell env: must be ignored.
            stack.enter_context(
                patch.dict(
                    "os.environ",
                    {"CYBERWAVE_ENVIRONMENT": "dev", "ZENOH_CONNECT": "tcp/other:7447"},
                    clear=False,
                )
            )
            checks = wm._collect_preflight_checks(wd)
        named = {c.name: c for c in checks}
        assert named["env-consistency"].level == "ok", named["env-consistency"].message


def test_env_consistency_flags_driver_worker_mismatch() -> None:
    """A running worker whose env disagrees with the driver is a warning."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text("import cw\n")
        (wd / "w.py").chmod(0o644)
        envs = {
            "cyberwave-driver-a": {
                "CYBERWAVE_ENVIRONMENT": "prod",
                "ZENOH_CONNECT": "tcp/router:7447",
                "CYBERWAVE_TWIN_UUID": "t1",
            },
            "cyberwave-worker-x": {
                "CYBERWAVE_ENVIRONMENT": "dev",
                "ZENOH_CONNECT": "tcp/router:7447",
            },
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(wm, "_find_edge_core_binary", return_value="/usr/bin/cwec")
            )
            _apply_docker_patches(
                stack,
                wm,
                drivers=["cyberwave-driver-a"],
                workers=["cyberwave-worker-x"],
                envs=envs,
            )
            checks = wm._collect_preflight_checks(wd)
        named = {c.name: c for c in checks}
        assert named["env-consistency"].level == "warn"
        assert "worker/driver disagreement" in (named["env-consistency"].hint or "")


# ---------------------------------------------------------------------------
# doctor command
# ---------------------------------------------------------------------------


def test_doctor_exits_nonzero_on_blocking_issue() -> None:
    """``cyberwave worker doctor`` exits 1 when any check is an error."""
    wm = _module()
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        workers_dir = Path(tmp) / "workers"
        workers_dir.mkdir()
        with (
            patch.object(wm, "WORKERS_DIR", workers_dir),
            patch.object(wm, "_find_edge_core_binary", return_value=None),
            patch.object(wm.shutil, "which", return_value=None),
        ):
            result = runner.invoke(wm.worker, ["doctor", "--no-runtime"])
        assert result.exit_code == 1, result.output
        assert "edge-core" in result.output


def test_doctor_exits_zero_when_only_warnings() -> None:
    """Warnings alone (e.g. no driver yet) must not fail doctor."""
    wm = _module()
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        workers_dir = Path(tmp) / "workers"
        workers_dir.mkdir()
        f = workers_dir / "w.py"
        f.write_text("import cw\n")
        f.chmod(0o644)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(wm, "WORKERS_DIR", workers_dir))
            stack.enter_context(
                patch.object(wm, "_find_edge_core_binary", return_value="/usr/bin/cwec")
            )
            _apply_docker_patches(stack, wm, drivers=[])
            result = runner.invoke(wm.worker, ["doctor", "--no-runtime"])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# doctor: legacy env var detection
# ---------------------------------------------------------------------------


def test_preflight_flags_legacy_zenoh_shm_env() -> None:
    """The canonical env is ``ZENOH_SHARED_MEMORY``. If a container has
    the legacy/typo'd ``ZENOH_SHM_ENABLED`` we must surface a warning."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text("import cw\n")
        (wd / "w.py").chmod(0o644)
        envs = {
            "cyberwave-driver-a": {
                "CYBERWAVE_ENVIRONMENT": "prod",
                "ZENOH_SHM_ENABLED": "true",
            },
        }
        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(wm, "_find_edge_core_binary", return_value="/usr/bin/cwec")
            )
            _apply_docker_patches(stack, wm, drivers=list(envs), envs=envs)
            checks = wm._collect_preflight_checks(wd)
        named = {c.name: c for c in checks}
        assert named.get("env-legacy-names") is not None
        assert named["env-legacy-names"].level == "warn"
        assert "ZENOH_SHARED_MEMORY" in (named["env-legacy-names"].hint or "")


# ---------------------------------------------------------------------------
# doctor: hook scanner (static AST scan of worker files)
# ---------------------------------------------------------------------------


_TWIN_A = "11111111-1111-1111-1111-111111111111"
_TWIN_B = "22222222-2222-2222-2222-222222222222"


def test_hook_scanner_resolves_literal_uuid_and_sensor() -> None:
    """Literal UUIDs and the ``sensor=`` kwarg flow through to the expected key."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "w.py"
        f.write_text(
            f"""
import cw

@cw.on_frame("{_TWIN_A}", sensor="front")
def handle(frame, ctx):
    pass
"""
        )
        bindings = wm._scan_hook_registrations(f)
    assert len(bindings) == 1
    b = bindings[0]
    assert b.twin_uuid == _TWIN_A
    assert b.channel == "frames"
    assert b.sensor == "front"
    assert b.expected_key == f"cw/{_TWIN_A}/data/frames/front"


def test_hook_scanner_resolves_module_level_constant() -> None:
    """UUID bound to a module-level string constant must be resolved."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "w.py"
        f.write_text(
            f"""
import cw

TWIN = "{_TWIN_A}"

@cw.on_joint_states(TWIN)
def handle(js, ctx):
    pass
"""
        )
        bindings = wm._scan_hook_registrations(f)
    assert len(bindings) == 1
    assert bindings[0].expected_key == f"cw/{_TWIN_A}/data/joint_states"


def test_hook_scanner_skips_unresolvable_uuids() -> None:
    """Non-literal, non-module-level UUIDs are skipped (rather than guessed)."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "w.py"
        f.write_text(
            """
import os
import cw

@cw.on_frame(os.environ["TWIN"])
def handle(frame, ctx):
    pass
"""
        )
        bindings = wm._scan_hook_registrations(f)
    assert bindings == []


# ---------------------------------------------------------------------------
# doctor: runtime checks (keyexpr alignment with mocked zenoh probe)
# ---------------------------------------------------------------------------


def test_runtime_checks_flag_wrong_twin_publisher() -> None:
    """If the driver publishes on a different twin than the hook listens on,
    keyexpr-alignment must warn with a root-cause pointer."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text(
            f'''import cw

@cw.on_frame("{_TWIN_A}", sensor="default")
def handle(frame, ctx):
    pass
'''
        )

        seen = {
            # Driver is putting frames under a DIFFERENT twin UUID.
            f"cw/{_TWIN_B}/data/frames/default": 150,
            "cw/_monitor/worker_stats": 3,
        }
        with (
            patch.object(wm, "_probe_zenoh_bus", return_value=(True, seen, None)),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["zenoh-liveness"].level == "ok"
    assert named["keyexpr-alignment"].level == "warn"
    # The hint must surface the "same channel, different twin" diagnosis.
    hint = named["keyexpr-alignment"].hint or ""
    assert _TWIN_B in hint or "different twin" in hint


def test_runtime_checks_pass_when_hook_sees_traffic() -> None:
    """Matching publisher traffic must produce an OK keyexpr-alignment check."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text(
            f'''import cw

@cw.on_frame("{_TWIN_A}")
def handle(frame, ctx):
    pass
'''
        )

        seen = {f"cw/{_TWIN_A}/data/frames/default": 120}
        with (
            patch.object(wm, "_probe_zenoh_bus", return_value=(True, seen, None)),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["keyexpr-alignment"].level == "ok"


def test_runtime_checks_flag_unscoped_keys() -> None:
    """A publisher putting to ``frames/color_camera`` (no twin prefix) must
    be called out, because twin-scoped hooks will silently drop it."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        seen = {"frames/color_camera": 90}
        with (
            patch.object(wm, "_probe_zenoh_bus", return_value=(True, seen, None)),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["keyexpr-scoping"].level == "warn"
    assert "frames/color_camera" in (named["keyexpr-scoping"].hint or "")


def test_runtime_checks_warn_on_silent_bus() -> None:
    """Zero messages in the probe window is a warning, not a pass."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        with (
            patch.object(wm, "_probe_zenoh_bus", return_value=(True, {}, None)),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["zenoh-liveness"].level == "warn"


def test_runtime_checks_info_when_zenoh_not_installed() -> None:
    """Missing eclipse-zenoh should degrade gracefully to an info check."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        with (
            patch.object(
                wm,
                "_probe_zenoh_bus",
                return_value=(False, {}, "missing-dep:eclipse-zenoh"),
            ),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["zenoh-liveness"].level == "info"


def test_runtime_checks_warn_on_session_open_error() -> None:
    """A Zenoh connect error (session open failure) must warn, not crash."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        with (
            patch.object(
                wm,
                "_probe_zenoh_bus",
                return_value=(False, {}, "Connection refused"),
            ),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["zenoh-liveness"].level == "warn"
    assert "Connection refused" in (named["zenoh-liveness"].message or "")
    # And no downstream keyexpr-* checks (the probe failed).
    assert "keyexpr-alignment" not in named
    assert "keyexpr-scoping" not in named


def test_runtime_checks_admin_only_bus_is_silent() -> None:
    """A bus with only Zenoh admin/liveliness traffic (``@/...``) is
    effectively silent — no driver is publishing — and must be flagged
    as such rather than showing an "ok" liveness with admin keys in the
    top-5 list."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        seen = {
            "@/liveliness/router/abc": 8,
            "@/meta/keyexpr/defs": 2,
        }
        with (
            patch.object(wm, "_probe_zenoh_bus", return_value=(True, seen, None)),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["zenoh-liveness"].level == "warn"
    # Admin-only scenario must be called out explicitly so the user knows
    # "reachable but no publisher".
    assert "admin" in (named["zenoh-liveness"].hint or "").lower()
    # No bogus keyexpr-* checks when there's no app traffic.
    assert "keyexpr-alignment" not in named
    assert "keyexpr-scoping" not in named


def test_runtime_checks_top_keys_prioritize_data_over_admin() -> None:
    """Data keys must show up at the top of the liveness display even when
    admin heartbeats are noisier."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        seen = {
            "@/liveliness/router/abc": 500,  # very chatty admin
            f"cw/{_TWIN_A}/data/frames/default": 10,  # real data, lower count
        }
        with (
            patch.object(wm, "_probe_zenoh_bus", return_value=(True, seen, None)),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["zenoh-liveness"].level == "ok"
    hint = named["zenoh-liveness"].hint or ""
    # The data key comes first in Top keys, above the admin key, despite
    # the admin key having 50x the traffic.
    data_pos = hint.find(f"cw/{_TWIN_A}/data/frames/default")
    admin_pos = hint.find("@/liveliness/router/abc")
    assert data_pos != -1 and admin_pos != -1
    assert data_pos < admin_pos, hint


def test_runtime_checks_silent_bus_skips_alignment() -> None:
    """Silent bus must short-circuit — a duplicate "no matching publisher"
    warning would just add noise."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text(
            f'''import cw

@cw.on_frame("{_TWIN_A}")
def handle(frame, ctx):
    pass
'''
        )
        with (
            patch.object(wm, "_probe_zenoh_bus", return_value=(True, {}, None)),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["zenoh-liveness"].level == "warn"
    assert "keyexpr-alignment" not in named
    assert "keyexpr-scoping" not in named


def test_runtime_checks_require_exact_key_match() -> None:
    """Subscribers attach to literal keys — extra trailing segments on the
    published key must NOT count as a match (the SDK wouldn't deliver it)."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text(
            f'''import cw

@cw.on_frame("{_TWIN_A}", sensor="default")
def handle(frame, ctx):
    pass
'''
        )

        # Publisher puts on a *longer* key than the hook expects. Zenoh
        # literal-key subscriptions would silently drop this, so alignment
        # must flag it as unmatched.
        seen = {f"cw/{_TWIN_A}/data/frames/default/extra": 90}
        with (
            patch.object(wm, "_probe_zenoh_bus", return_value=(True, seen, None)),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["keyexpr-alignment"].level == "warn"


def test_runtime_checks_flag_sensor_mismatch() -> None:
    """The user's original bug: hook listens on default sensor, driver
    publishes on color_camera. Must be diagnosed specifically."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "w.py").write_text(
            f'''import cw

@cw.on_frame("{_TWIN_A}")
def handle(frame, ctx):
    pass
'''
        )

        seen = {f"cw/{_TWIN_A}/data/frames/color_camera": 120}
        with (
            patch.object(wm, "_probe_zenoh_bus", return_value=(True, seen, None)),
            patch.object(wm, "_running_driver_containers", return_value=[]),
            patch.object(wm, "_running_worker_containers", return_value=[]),
        ):
            checks = wm._collect_runtime_checks(wd, duration=0.1)
    named = {c.name: c for c in checks}
    assert named["keyexpr-alignment"].level == "warn"
    hint = named["keyexpr-alignment"].hint or ""
    assert "Sensor mismatch" in hint
    assert f"cw/{_TWIN_A}/data/frames/color_camera" in hint


def test_canonical_key_parser_accepts_valid_and_rejects_invalid() -> None:
    """Unit guard around _parse_canonical_key / _CANONICAL_KEY_RE."""
    wm = _module()
    p = wm._parse_canonical_key(f"cw/{_TWIN_A}/data/frames/default")
    assert p is not None
    assert p.twin == _TWIN_A and p.channel == "frames" and p.sensor == "default"

    p_nosens = wm._parse_canonical_key(f"cw/{_TWIN_A}/data/joint_states")
    assert p_nosens is not None
    assert p_nosens.sensor is None

    # Too many trailing segments — not canonical.
    assert (
        wm._parse_canonical_key(f"cw/{_TWIN_A}/data/frames/default/extra") is None
    )
    # Non-UUID "twin".
    assert wm._parse_canonical_key("cw/camera/data/frames") is None
    # Bare key (the case that caused the user's silent failure).
    assert wm._parse_canonical_key("frames/color_camera") is None
    # Uppercase channel — not canonical per the SDK's validator.
    assert wm._parse_canonical_key(f"cw/{_TWIN_A}/data/Frames") is None


def test_listen_to_loopback_connect_handles_variants() -> None:
    """ZENOH_LISTEN parsing: TCP IPv4/IPv6 get rewritten, others bail out."""
    wm = _module()
    assert (
        wm._listen_to_loopback_connect("tcp/0.0.0.0:7447")
        == "tcp/127.0.0.1:7447"
    )
    assert (
        wm._listen_to_loopback_connect("tcp/[::]:7447") == "tcp/[::1]:7447"
    )
    assert (
        wm._listen_to_loopback_connect("tcp/192.168.1.4:7447")
        == "tcp/192.168.1.4:7447"
    )
    # Non-TCP and empty/comma-list inputs are skipped — we can't safely
    # rewrite them to a loopback form.
    assert wm._listen_to_loopback_connect("udp/0.0.0.0:7447") is None
    assert wm._listen_to_loopback_connect("quic/0.0.0.0:7447") is None
    assert wm._listen_to_loopback_connect("") is None
    assert (
        wm._listen_to_loopback_connect(
            "tcp/0.0.0.0:7447,tcp/192.168.1.4:7447"
        )
        is None
    )


def test_scanner_resolves_annotated_module_constant() -> None:
    """``TWIN: str = "uuid"`` (PEP 526 annotation) must still be resolved."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "w.py"
        f.write_text(
            f"""
import cw

TWIN: str = "{_TWIN_A}"

@cw.on_frame(TWIN, sensor="color_camera")
def handle(frame, ctx):
    pass
"""
        )
        bindings = wm._scan_hook_registrations(f)
    assert len(bindings) == 1
    assert bindings[0].expected_key == f"cw/{_TWIN_A}/data/frames/color_camera"
