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
            result = runner.invoke(wm.worker, ["doctor", "--no-probe"])
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
            result = runner.invoke(wm.worker, ["doctor", "--no-probe"])
        assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# keyexpr-intersection probe
# ---------------------------------------------------------------------------


def test_scan_frame_hooks_extracts_twin_and_sensor() -> None:
    """``_scan_frame_hooks`` must recover both explicit and wildcard hooks."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "wf_demo.py"
        f.write_text(
            "import cyberwave as cw\n"
            "\n"
            "@cw.on_frame('487d1591-e3bf-47e4-bf1c-30c0d74f8d7e', "
            "sensor='color_camera')\n"
            "def a(frame): pass\n"
            "\n"
            "@cw.on_frame('00000000-0000-0000-0000-000000000001')\n"
            "def b(frame): pass\n"
            "\n"
            "@cw.on_frame('00000000-0000-0000-0000-000000000002', sensor='*')\n"
            "def c(frame): pass\n"
        )
        hooks = wm._scan_frame_hooks(f)
    assert hooks == [
        ("487d1591-e3bf-47e4-bf1c-30c0d74f8d7e", "color_camera"),
        ("00000000-0000-0000-0000-000000000001", None),
        ("00000000-0000-0000-0000-000000000002", None),
    ]


def test_keyexpr_intersects_handles_wildcards() -> None:
    wm = _module()
    assert wm._keyexpr_intersects(
        "cw/abc/data/frames/**", "cw/abc/data/frames/color_camera"
    )
    assert not wm._keyexpr_intersects(
        "cw/abc/data/frames/default", "cw/abc/data/frames/color_camera"
    )
    assert wm._keyexpr_intersects(
        "cw/abc/data/frames/color_camera", "cw/abc/data/frames/color_camera"
    )
    assert wm._keyexpr_intersects(
        "cw/abc/data/frames/*", "cw/abc/data/frames/front"
    )
    # ``**`` matches zero segments too — ``frames/**`` matches bare ``frames``.
    assert wm._keyexpr_intersects("cw/abc/data/frames/**", "cw/abc/data/frames")
    # Expected longer than observed and not a wildcard → no match.
    assert not wm._keyexpr_intersects(
        "cw/abc/data/frames/front", "cw/abc/data/frames"
    )
    # ``*`` matches exactly one segment.
    assert not wm._keyexpr_intersects("cw/abc/data/frames/*", "cw/abc/data/frames")


def test_probe_flags_stale_default_sensor() -> None:
    """The classic drift: hook pins ``sensor='default'`` but the driver
    publishes under ``frames/color_camera``.  Probe must warn."""
    wm = _module()
    twin = "487d1591-e3bf-47e4-bf1c-30c0d74f8d7e"
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "wf_stale.py").write_text(
            f"import cyberwave as cw\n"
            f"@cw.on_frame('{twin}', sensor='default')\n"
            f"def h(frame): pass\n"
        )
        observed = {f"cw/{twin}/data/frames/color_camera"}
        with (
            patch.object(
                wm,
                "_observe_zenoh_keys",
                return_value=(True, observed, ""),
            ),
            patch.object(wm, "_find_worker_container", return_value=None),
            patch.object(wm, "_running_driver_containers", return_value=[]),
        ):
            checks = wm._collect_keyexpr_probe_checks(wd, duration=0.0)
    named = {c.name: c for c in checks}
    assert "keyexpr-probe" in named
    c = named["keyexpr-probe"]
    assert c.level == "warn", c.message
    hint = c.hint or ""
    assert "frames/default" in hint
    assert "frames/color_camera" in hint


def test_probe_ok_when_wildcard_matches_any_camera() -> None:
    """An ``@cw.on_frame(twin)`` with no sensor must match driver-published
    ``frames/<real_sensor>`` keys."""
    wm = _module()
    twin = "00000000-0000-0000-0000-000000000001"
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "wf_wild.py").write_text(
            f"import cyberwave as cw\n"
            f"@cw.on_frame('{twin}')\n"
            f"def h(frame): pass\n"
        )
        observed = {f"cw/{twin}/data/frames/front"}
        with (
            patch.object(
                wm,
                "_observe_zenoh_keys",
                return_value=(True, observed, ""),
            ),
            patch.object(wm, "_find_worker_container", return_value=None),
            patch.object(wm, "_running_driver_containers", return_value=[]),
        ):
            checks = wm._collect_keyexpr_probe_checks(wd, duration=0.0)
    named = {c.name: c for c in checks}
    assert named["keyexpr-probe"].level == "ok", named["keyexpr-probe"].message


def test_probe_is_info_when_zenoh_unavailable() -> None:
    """When Zenoh can't be loaded the probe is skipped, not a failure."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "wf_x.py").write_text(
            "import cyberwave as cw\n"
            "@cw.on_frame('00000000-0000-0000-0000-000000000001')\n"
            "def h(frame): pass\n"
        )
        with (
            patch.object(
                wm,
                "_observe_zenoh_keys",
                return_value=(False, set(), "zenoh not installed"),
            ),
            patch.object(wm, "_find_worker_container", return_value=None),
            patch.object(wm, "_running_driver_containers", return_value=[]),
        ):
            checks = wm._collect_keyexpr_probe_checks(wd, duration=0.0)
    named = {c.name: c for c in checks}
    assert named["keyexpr-probe"].level == "info"


def test_probe_no_hooks_returns_info() -> None:
    """Probe short-circuits with info when no hooks are installed."""
    wm = _module()
    with tempfile.TemporaryDirectory() as tmp:
        wd = Path(tmp) / "workers"
        wd.mkdir()
        (wd / "plain.py").write_text("print('no hooks here')\n")
        checks = wm._collect_keyexpr_probe_checks(wd, duration=0.0)
    named = {c.name: c for c in checks}
    assert named["keyexpr-probe"].level == "info"


# ---------------------------------------------------------------------------
# one-driver ⇄ one-twin invariant
# ---------------------------------------------------------------------------


def test_twin_uuids_from_keys_strips_segment() -> None:
    """Only keys matching ``cw/<twin>/data/...`` contribute a twin UUID.
    Monitor/internal keys (no ``/data/`` segment) are excluded."""
    wm = _module()
    assert wm._twin_uuids_from_keys(
        {
            "cw/aaa/data/frames/color_camera",
            "cw/bbb/data/frames/color_camera",
            "cw/_monitor/worker_stats",
        }
    ) == {"aaa", "bbb"}


def test_binding_ok_when_each_driver_owns_one_twin() -> None:
    """Two drivers, two twins, each container serves only its twin."""
    wm = _module()
    checks = wm._collect_driver_twin_binding_checks(
        observed_twins={"aaa", "bbb"},
        driver_envs={
            "cyberwave-driver-1": {"CYBERWAVE_TWIN_UUID": "aaa"},
            "cyberwave-driver-2": {"CYBERWAVE_TWIN_UUID": "bbb"},
        },
    )
    assert len(checks) == 1
    assert checks[0].level == "ok"
    assert "2 driver(s)" in checks[0].message


def test_binding_flags_two_drivers_bound_to_same_twin() -> None:
    """Two containers claim the same twin — violates 1-to-1."""
    wm = _module()
    checks = wm._collect_driver_twin_binding_checks(
        observed_twins={"aaa"},
        driver_envs={
            "cyberwave-driver-1": {"CYBERWAVE_TWIN_UUID": "aaa"},
            "cyberwave-driver-2": {"CYBERWAVE_TWIN_UUID": "aaa"},
        },
    )
    assert len(checks) == 1
    assert checks[0].level == "warn"
    hint = checks[0].hint or ""
    assert "cyberwave-driver-1" in hint
    assert "cyberwave-driver-2" in hint
    assert "served by 2 drivers" in hint


def test_binding_flags_rogue_publisher() -> None:
    """Keys observed under a twin nobody is bound to."""
    wm = _module()
    checks = wm._collect_driver_twin_binding_checks(
        observed_twins={"aaa", "rogue-twin"},
        driver_envs={
            "cyberwave-driver-1": {"CYBERWAVE_TWIN_UUID": "aaa"},
        },
    )
    assert len(checks) == 1
    assert checks[0].level == "warn"
    assert "rogue-twin" in (checks[0].hint or "")


def test_binding_empty_inputs_returns_nothing() -> None:
    """No containers and no observations → no check."""
    wm = _module()
    assert wm._collect_driver_twin_binding_checks(set(), {}) == []


def test_binding_info_when_traffic_but_no_docker_bindings() -> None:
    """Traffic seen on the bus but no driver container envs to cross-check
    against — doctor must report ``info`` rather than confidently ``ok``."""
    wm = _module()
    checks = wm._collect_driver_twin_binding_checks(
        observed_twins={"aaa"},
        driver_envs={},
    )
    assert len(checks) == 1
    assert checks[0].level == "info"
    assert "Skipped" in checks[0].message


def test_binding_info_when_driver_env_has_no_twin_uuid() -> None:
    """Driver container exists but CYBERWAVE_TWIN_UUID isn't set — we can't
    enforce the invariant, so emit ``info`` not ``ok``."""
    wm = _module()
    checks = wm._collect_driver_twin_binding_checks(
        observed_twins={"aaa"},
        driver_envs={"cyberwave-driver-1": {"OTHER": "val"}},
    )
    assert len(checks) == 1
    assert checks[0].level == "info"
