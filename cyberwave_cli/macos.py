"""macOS-specific helpers for Cyberwave Edge.

Handles USB/IP server setup so Docker Desktop containers can access
host USB devices (e.g. serial motor controllers) via USB/IP passthrough.

Also provides an optional MJPEG camera stream server for cases where
USB/IP video bandwidth is insufficient (cameras are forwarded as an
HTTP MJPEG stream instead of raw USB passthrough).

Includes a launchd LaunchAgent for edge-core so that the ``cyberwave edge``
CLI commands (start/stop/restart/status/logs) work identically to
the systemd-based experience on Linux.
"""

from __future__ import annotations

import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, Optional
from xml.sax.saxutils import escape as _xml_escape

logger = logging.getLogger(__name__)

from cyberwave.edge.platform import (
    USBIP_LAUNCHD_LABEL,
    USBIP_PORT,
    is_usbip_server_running,
)
from cyberwave.edge.platform import (
    is_port_listening as _is_port_listening,
)
from rich.console import Console

from .config import _resolve_sudo_user_home, clean_subprocess_env

USBIP_REPO_URL = "https://github.com/jiegec/usbip.git"

_USBIP_LAUNCHD_PLIST_TEMPLATE = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
      "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>
        <string>{label}</string>
        <key>ProgramArguments</key>
        <array>
            <string>{wrapper_path}</string>
        </array>
        <key>RunAtLoad</key>
        <true/>
        <key>KeepAlive</key>
        <true/>
        <key>StandardOutPath</key>
        <string>{log_path}</string>
        <key>StandardErrorPath</key>
        <string>{log_path}</string>
    </dict>
    </plist>
""")

# Singleton console is set via ``init_console`` so macos.py shares the same
# instance as core.py (important for monkeypatch-based tests).
_console: Optional[Console] = None


def init_console(console: Console) -> None:
    """Inject the shared Console instance used by core.py."""
    global _console
    _console = console


def _get_console() -> Console:
    if _console is None:
        return Console()
    return _console


# ---- path helpers ------------------------------------------------------------
# All paths are resolved at *call time* (not import time) so that running under
# ``sudo`` correctly resolves the invoking user's home directory.


def _user_home() -> Path:
    """Return the real user's home, even when running via sudo."""
    sudo_home = _resolve_sudo_user_home()
    return sudo_home or Path.home()


def _usbip_install_dir() -> Path:
    return _user_home() / ".cyberwave" / "usbip"


def _usbip_binary_path() -> Path:
    return _usbip_install_dir() / "target" / "release" / "examples" / "host"


def _usbip_wrapper_path() -> Path:
    return _user_home() / ".cyberwave" / "usbip_wrapper.sh"


def _usbip_launchd_plist() -> Path:
    return _user_home() / "Library" / "LaunchAgents" / f"{USBIP_LAUNCHD_LABEL}.plist"


def _usbip_log_path() -> Path:
    return _user_home() / ".cyberwave" / "usbip.log"


# ---- helpers -----------------------------------------------------------------


def is_macos() -> bool:
    return platform.system() == "Darwin"


def _has_cargo() -> bool:
    return shutil.which("cargo") is not None


def _has_git() -> bool:
    return shutil.which("git") is not None


def is_usbip_server_installed() -> bool:
    return _usbip_binary_path().is_file()


def _run(cmd: list[str], *, check: bool = True, **kwargs: Any) -> subprocess.CompletedProcess:
    _get_console().print(f"[dim]$ {' '.join(cmd)}[/dim]")
    kwargs.setdefault("env", clean_subprocess_env())
    return subprocess.run(cmd, check=check, **kwargs)


def _launchctl_as_user(args: list[str]) -> list[str]:
    """Build a launchctl command that targets the real user's domain.

    When running as root (e.g. via ``sudo cyberwave edge install``),
    ``launchctl bootstrap gui/<uid>`` fails because root can't
    register into another user's GUI domain.  Wrapping with
    ``sudo -u <real_user>`` drops privileges so launchd accepts the
    request.
    """
    if os.getuid() == 0:
        username, _, _ = _resolve_real_user()
        if username:
            logger.debug("Dropping to user %s for launchctl %s", username, args)
            return ["sudo", "-u", username, "launchctl", *args]
    return ["launchctl", *args]


# ---- launchd timing helpers --------------------------------------------------
# ``launchctl bootout`` is asynchronous: it returns immediately while launchd
# continues unloading the service.  If a subsequent ``bootstrap`` happens before
# launchd has released the label, it fails with the notoriously generic
# ``5: Input/output error``.  These helpers actively wait for launchd to settle
# instead of relying on fixed sleeps.


def _launchd_label_is_loaded(target: str) -> bool:
    """Return True when launchctl can still see ``<domain>/<label>``."""
    try:
        result = subprocess.run(
            _launchctl_as_user(["print", target]),
            capture_output=True,
            timeout=5,
            env=clean_subprocess_env(),
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def wait_for_launchd_unload(
    label: str,
    *,
    timeout: float = 10.0,
    domains: Optional[tuple[str, ...]] = None,
    legacy_labels: tuple[str, ...] = (),
) -> bool:
    """Bootout *label* across the relevant domains and wait until launchd
    has fully released it.

    Polls ``launchctl print`` until the label is gone, so callers can chain
    ``bootstrap`` without hitting the transient exit-5 race.

    Args:
        label: The launchd label (e.g. ``com.cyberwave.edge.core``).
        timeout: Maximum total time to wait for unload, in seconds.
        domains: Explicit list of ``<domain>`` strings to bootout from.
            Defaults to ``("gui/<real-uid>", "system")``.
        legacy_labels: Historical labels that should also be booted out
            (best-effort, no wait for unload).  Used to migrate users from
            older CLI versions that registered the same logical service
            under a different label.

    Returns:
        True if the label is unloaded (or was never loaded) by the deadline,
        False if it is still loaded when the deadline elapses.
    """
    if domains is None:
        _, real_uid, _ = _resolve_real_user()
        gui_domain = f"gui/{real_uid}" if real_uid is not None else None
        domains = tuple(d for d in (gui_domain, "system") if d)

    for target_label in (label, *legacy_labels):
        for domain in domains:
            target = f"{domain}/{target_label}"
            try:
                subprocess.run(
                    _launchctl_as_user(["bootout", target]),
                    capture_output=True,
                    timeout=10,
                    env=clean_subprocess_env(),
                )
            except (subprocess.TimeoutExpired, OSError):
                pass

    deadline = time.monotonic() + timeout
    poll_interval = 0.1
    # ``announced`` keeps the CLI quiet on the happy path (launchd already
    # released the label by the time we poll) and only surfaces a one-shot
    # status line when the user is actually about to sit through the wait.
    announced = False
    while time.monotonic() < deadline:
        still_loaded = any(
            _launchd_label_is_loaded(f"{domain}/{label}") for domain in domains
        )
        if not still_loaded:
            return True
        if not announced:
            _get_console().print(
                f"[dim]Waiting for launchd to release {label} "
                f"(up to {timeout:.0f}s)...[/dim]"
            )
            announced = True
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, 0.5)
    return False


def bootstrap_launchd_service(
    domain: str,
    plist_path: Path,
    *,
    retries: int = 2,
    retry_initial_delay: float = 0.5,
) -> None:
    """Run ``launchctl bootstrap <domain> <plist>`` with retry on transient
    I/O errors.

    Exit code 5 ("Input/output error") is launchd's catch-all for stale-state
    races: the previous service hasn't been fully released yet, the
    spawn-throttle window has not elapsed, etc.  Retrying after a short
    backoff resolves the vast majority of these cases.  Other exit codes
    (permission denied, malformed plist, missing binary) are surfaced
    immediately without retry.

    Raises:
        subprocess.CalledProcessError: If bootstrap fails on the final
            attempt, or fails with a non-transient error.
        FileNotFoundError: If ``launchctl`` itself is missing.
    """
    # The loop body always either ``return``s on success or ``raise``s on the
    # terminal attempt (final retry exhausted, or non-transient exit code), so
    # no fall-through path can reach the end of the function — keeping a
    # ``last_exc`` accumulator + post-loop ``raise`` would be unreachable.
    delay = retry_initial_delay
    for attempt in range(retries + 1):
        try:
            _run(_launchctl_as_user(["bootstrap", domain, str(plist_path)]))
            return
        except subprocess.CalledProcessError as exc:
            if exc.returncode != 5 or attempt == retries:
                raise
            _get_console().print(
                f"[yellow]launchctl bootstrap hit a transient I/O error, "
                f"retrying in {delay:.1f}s "
                f"(attempt {attempt + 2}/{retries + 1})...[/yellow]"
            )
            time.sleep(delay)
            delay *= 2


def _resolve_real_user() -> tuple[Optional[str], Optional[int], Optional[int]]:
    """Return (username, uid, gid) for the real user, even under sudo."""
    sudo_user = os.getenv("SUDO_USER", "").strip()
    if sudo_user:
        try:
            import pwd

            pw = pwd.getpwnam(sudo_user)
            return sudo_user, pw.pw_uid, pw.pw_gid
        except Exception:
            pass
    uid = os.getuid()
    try:
        import pwd

        pw = pwd.getpwuid(uid)
        return pw.pw_name, pw.pw_uid, pw.pw_gid
    except Exception:
        return None, uid, None


def _chown_to_real_user(path: Path, *, recursive: bool = False) -> None:
    """Best-effort chown to the invoking user (relevant when running under sudo).

    For non-recursive single-path chown, delegates to the shared
    :func:`~cyberwave_cli.config.chown_to_sudo_user` helper.  The recursive
    variant (used for compiled binary directories) walks the tree locally.
    """
    if not recursive:
        from .config import chown_to_sudo_user

        chown_to_sudo_user(path)
        return
    _, uid, gid = _resolve_real_user()
    if uid is None:
        return
    try:
        os.chown(path, uid, gid or -1)
        for child in path.rglob("*"):
            try:
                os.chown(child, uid, gid or -1)
            except OSError:
                pass
    except OSError:
        pass


def _fix_user_dir_ownership(dir_path: Path) -> None:
    """Reclaim ownership of *dir_path* if it was left root-owned by a prior sudo run."""
    username, real_uid, _ = _resolve_real_user()
    if real_uid is None:
        return
    try:
        stat = dir_path.stat()
        if stat.st_uid != real_uid:
            logger.debug("Reclaiming ownership of %s (uid %d -> %d)", dir_path, stat.st_uid, real_uid)
            subprocess.run(
                ["sudo", "chown", username or str(real_uid), str(dir_path)],
                check=True,
                capture_output=True,
            )
    except (OSError, subprocess.CalledProcessError):
        pass


def _write_file_as_real_user(
    path: Path, contents: str, *, mode: Optional[int] = None
) -> None:
    """Write *contents* to *path*, handling ownership mismatches.

    When running as root (``sudo``), temporarily drops the effective
    UID/GID to the invoking user so that files in ``~/Library/`` are
    created with the correct owner.

    When a prior ``sudo`` run left the parent directory or target file
    owned by root, reclaims ownership before writing.
    """
    _, real_uid, real_gid = _resolve_real_user()
    my_uid = real_uid if real_uid is not None else os.getuid()
    need_drop = os.getuid() == 0 and real_uid is not None and real_uid != 0

    # Fix parent directory ownership if it's owned by root (common after
    # a previous ``sudo cyberwave edge install``).
    parent = path.parent
    if parent.exists():
        _fix_user_dir_ownership(parent)

    # Remove an existing file owned by a different user.
    if path.exists():
        try:
            stat = path.stat()
            if stat.st_uid != my_uid:
                try:
                    path.unlink()
                except OSError:
                    logger.debug("Removing root-owned file %s via sudo rm", path)
                    subprocess.run(
                        ["sudo", "rm", "-f", str(path)],
                        check=True,
                        capture_output=True,
                    )
        except (OSError, subprocess.CalledProcessError):
            pass

    saved_euid = os.geteuid()
    saved_egid = os.getegid()

    try:
        if need_drop:
            os.setegid(real_gid or saved_egid)
            os.seteuid(real_uid)  # type: ignore[arg-type]

        parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        if mode is not None:
            path.chmod(mode)
    finally:
        if need_drop:
            os.seteuid(saved_euid)
            os.setegid(saved_egid)


def _strip_xattrs(path: Path) -> None:
    """Remove quarantine / provenance xattrs that block launchd execution."""
    try:
        subprocess.run(
            ["xattr", "-cr", str(path)],
            capture_output=True,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        pass


# ---- USB/IP server -----------------------------------------------------------


def _install_usbip_server() -> bool:
    """Build the jiegec/usbip host server from source using cargo.

    Returns True on success.
    """
    console = _get_console()

    if not _has_git():
        console.print(
            "[red]git is required to clone the USB/IP server source.[/red]\n"
            "[dim]Install Xcode command-line tools: xcode-select --install[/dim]"
        )
        return False

    if not _has_cargo():
        console.print(
            "[red]Rust (cargo) is required to build the USB/IP server.[/red]\n"
            "[dim]Install Rust: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh[/dim]"
        )
        return False

    binary_path = _usbip_binary_path()
    if binary_path.is_file():
        console.print("[green]USB/IP server binary already built.[/green]")
        return True

    install_dir = _usbip_install_dir()
    console.print("[cyan]Building USB/IP server from source...[/cyan]")
    install_dir.parent.mkdir(parents=True, exist_ok=True)

    if not install_dir.exists():
        try:
            _run(["git", "clone", USBIP_REPO_URL, str(install_dir)])
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]Failed to clone usbip repo (exit {exc.returncode}).[/red]")
            return False

    # Preserve PATH (so cargo is findable) and HOME (so ~/.cargo works).
    build_env = clean_subprocess_env()
    for key in ("PATH", "HOME", "USER", "CARGO_HOME", "RUSTUP_HOME"):
        val = os.environ.get(key)
        if val:
            build_env[key] = val

    try:
        subprocess.run(
            ["cargo", "build", "--release", "--example", "host"],
            cwd=str(install_dir),
            check=True,
            env=build_env,
        )
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]cargo build failed (exit {exc.returncode}).[/red]")
        return False

    if not binary_path.is_file():
        console.print("[red]USB/IP server binary not found after build.[/red]")
        return False

    _strip_xattrs(binary_path)
    _chown_to_real_user(install_dir, recursive=True)

    console.print(f"[green]USB/IP server built:[/green] {binary_path}")
    return True


def _create_wrapper_script() -> bool:
    """Create a tiny shell wrapper around the host binary.

    launchd on recent macOS can refuse to execute unsigned Rust binaries
    directly (exit code 78).  A shell wrapper sidesteps this.
    """
    wrapper_path = _usbip_wrapper_path()
    binary_path = _usbip_binary_path()
    wrapper_contents = textwrap.dedent(f"""\
        #!/bin/bash
        exec "{binary_path}"
    """)
    try:
        wrapper_path.write_text(wrapper_contents)
        wrapper_path.chmod(0o755)
        _chown_to_real_user(wrapper_path)
        return True
    except OSError:
        _get_console().print(f"[red]Failed to create wrapper script at {wrapper_path}[/red]")
        return False


def _create_usbip_launchd_service() -> bool:
    """Create and load a launchd plist so the USB/IP server starts on login.

    Returns True on success.
    """
    console = _get_console()
    plist_path = _usbip_launchd_plist()
    log_path = _usbip_log_path()

    if not _create_wrapper_script():
        return False

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    plist_contents = _USBIP_LAUNCHD_PLIST_TEMPLATE.format(
        label=USBIP_LAUNCHD_LABEL,
        wrapper_path=str(_usbip_wrapper_path()),
        log_path=str(log_path),
    )

    try:
        plist_path.write_text(plist_contents)
    except OSError as exc:
        console.print(f"[red]Failed to write launchd plist: {exc}[/red]")
        return False

    _chown_to_real_user(plist_path)
    _chown_to_real_user(log_path)

    console.print(f"[green]Created:[/green] {plist_path}")

    _, real_uid, _ = _resolve_real_user()
    gui_domain = f"gui/{real_uid}" if real_uid is not None else None

    wait_for_launchd_unload(USBIP_LAUNCHD_LABEL)

    if gui_domain:
        try:
            bootstrap_launchd_service(gui_domain, plist_path)
        except subprocess.CalledProcessError:
            console.print("[yellow]launchctl bootstrap failed, falling back to load...[/yellow]")
            try:
                _run(_launchctl_as_user(["load", str(plist_path)]))
            except subprocess.CalledProcessError as exc:
                console.print(f"[red]Failed to load launchd service (exit {exc.returncode}).[/red]")
                return False
    else:
        try:
            _run(_launchctl_as_user(["load", str(plist_path)]))
        except subprocess.CalledProcessError as exc:
            console.print(f"[red]Failed to load launchd service (exit {exc.returncode}).[/red]")
            return False

    max_wait_secs = 10
    for i in range(max_wait_secs * 2):
        if _is_port_listening(USBIP_PORT):
            console.print(f"[green]USB/IP server is running ({USBIP_LAUNCHD_LABEL}).[/green]")
            break
        time.sleep(0.5)
    else:
        console.print(
            f"[yellow]USB/IP service loaded but port {USBIP_PORT} is not listening "
            f"after {max_wait_secs}s. Check logs at {log_path}[/yellow]"
        )
    return True


# ---- teardown ----------------------------------------------------------------


def _bootout_launchd_service(label: str) -> None:
    """Best-effort stop of a launchd service across gui and system domains."""
    _, real_uid, _ = _resolve_real_user()
    gui_domain = f"gui/{real_uid}" if real_uid is not None else None

    for bootout_target in [
        f"{gui_domain}/{label}" if gui_domain else None,
        f"system/{label}",
    ]:
        if bootout_target:
            try:
                subprocess.run(
                    _launchctl_as_user(["bootout", bootout_target]),
                    capture_output=True,
                    timeout=10,
                )
            except (subprocess.TimeoutExpired, OSError):
                pass


def _teardown_usbip_server() -> None:
    """Stop the USB/IP server and remove service artifacts.

    Removes the launchd plist, wrapper script, and log file but preserves the
    compiled binary under ``~/.cyberwave/usbip/`` so a subsequent install does
    not require re-cloning and recompiling from source.

    Best-effort: individual failures are logged but do not abort the teardown.
    """
    console = _get_console()
    console.print("[cyan]Tearing down existing USB/IP server...[/cyan]")

    _bootout_launchd_service(USBIP_LAUNCHD_LABEL)

    for path in [
        _usbip_launchd_plist(),
        _usbip_wrapper_path(),
        _usbip_log_path(),
    ]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            console.print(f"[yellow]Could not remove {path}[/yellow]")

    console.print("[green]USB/IP server teardown complete.[/green]")


# ---- public API --------------------------------------------------------------


def setup_usbip_server(*, force: bool = False) -> bool:
    """Install and start the USB/IP host server on macOS.

    This enables Docker Desktop containers to access USB devices
    (e.g. serial motor controllers) via USB/IP passthrough.

    When *force* is True, the existing installation is torn down first
    and rebuilt from scratch (equivalent to ``--force-reinstall``).

    Returns True on success.  Returns True immediately on non-macOS platforms.
    """
    if not is_macos():
        return True

    console = _get_console()

    if force:
        _teardown_usbip_server()
    elif is_usbip_server_running():
        console.print("[green]USB/IP server is already running.[/green]")
        return True

    console.print(
        "\n[bold]USB/IP Server Setup[/bold]\n"
        "Docker Desktop on macOS cannot pass USB devices directly to containers.\n"
        "USB/IP bridges this gap by sharing USB devices over the network.\n"
    )

    if not _install_usbip_server():
        return False

    return _create_usbip_launchd_service()


# ---- Camera stream server (MJPEG fallback) ----------------------------------
# When USB/IP video bandwidth is insufficient for cameras, this optional
# ffmpeg-based MJPEG server captures from macOS AVFoundation and serves
# an HTTP MJPEG stream that Docker containers consume via cv2.VideoCapture(url).

CAMERA_STREAM_LAUNCHD_LABEL = "com.cyberwave.camera-stream"
CAMERA_STREAM_PORT = 8091
# Prefix used for per-camera launchd labels when more than one physical
# camera is mapped on the host (e.g. ``com.cyberwave.camera-stream.1``).
CAMERA_STREAM_LAUNCHD_LABEL_PREFIX = f"{CAMERA_STREAM_LAUNCHD_LABEL}."
# Name of the JSON file that records per-twin stream URLs so edge-core can
# bind each driver container to the correct MJPEG endpoint.
CAMERA_STREAMS_FILENAME = "camera_streams.json"

_CAMERA_STREAM_WRAPPER_TEMPLATE = textwrap.dedent("""\
    #!/bin/bash
    # Cyberwave camera stream — captures from macOS camera and serves MJPEG.
    # launchd uses a minimal PATH; ensure Homebrew paths are included.
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
    DEVICE="${{CYBERWAVE_CAMERA_DEVICE:-0}}"
    PORT="${{CYBERWAVE_CAMERA_STREAM_PORT:-{port}}}"
    RESOLUTION="${{CYBERWAVE_CAMERA_STREAM_RESOLUTION:-1280x720}}"
    FPS="${{CYBERWAVE_CAMERA_STREAM_FPS:-30}}"

    # ffmpeg's ``-listen 1`` HTTP server is single-shot: when the consumer
    # disconnects, ffmpeg exits.  Looping in bash here keeps the stream alive
    # across reconnects and prevents launchd's spawn-throttle from disabling
    # the service after a short burst of restarts.
    trap 'exit 0' INT TERM

    # macOS ``logger`` routes to the unified log so each restart is visible
    # via ``log show --predicate 'process == "logger"' --info`` (or filter on
    # the ``cyberwave-camera-stream`` tag).  This makes a wedged loop (e.g.
    # ffmpeg crashing instantly because of a bad ``$DEVICE``) diagnosable
    # without grepping the StandardErrorPath file.
    logger -t cyberwave-camera-stream \\
        "Starting MJPEG server on port $PORT (device=$DEVICE, ${{RESOLUTION}}@${{FPS}})"

    # No ``set -e`` and no ``|| true`` here on purpose: we want to read
    # ffmpeg's real exit status into ``$?`` so the logger line can attribute
    # restarts (139 = SIGSEGV, 143 = SIGTERM from launchd bootout, 1 =
    # port-in-use / device-busy, etc.).  Wrapping with ``|| true`` would
    # always collapse it to 0 and hide the cause.
    while true; do
        ffmpeg -hide_banner -loglevel warning \\
            -fflags nobuffer -flags low_delay -avioflags direct \\
            -f avfoundation -framerate "$FPS" -video_size "$RESOLUTION" \\
            -thread_queue_size 1 -i "$DEVICE" \\
            -c:v mjpeg -q:v 5 \\
            -fflags nobuffer -flush_packets 1 \\
            -f mjpeg \\
            -listen 1 \\
            "http://0.0.0.0:$PORT"
        ffmpeg_status=$?
        logger -t cyberwave-camera-stream "ffmpeg exited (status $ffmpeg_status); restarting in 1s"
        sleep 1
    done
""")

_CAMERA_STREAM_PLIST_TEMPLATE = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
      "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>
        <string>{label}</string>
        <key>ProgramArguments</key>
        <array>
            <string>{wrapper_path}</string>
        </array>
        <key>EnvironmentVariables</key>
        <dict>
            <key>CYBERWAVE_CAMERA_DEVICE</key>
            <string>{device_name}</string>
        </dict>
        <key>RunAtLoad</key>
        <true/>
        <key>KeepAlive</key>
        <true/>
        <key>StandardOutPath</key>
        <string>{log_path}</string>
        <key>StandardErrorPath</key>
        <string>{log_path}</string>
    </dict>
    </plist>
""")


def _camera_stream_wrapper_path(slot: Optional[int] = None) -> Path:
    """Path to the ffmpeg wrapper script. ``slot`` differentiates per-camera services."""
    if slot is None or slot == 0:
        return _user_home() / ".cyberwave" / "camera_stream.sh"
    return _user_home() / ".cyberwave" / f"camera_stream.{slot}.sh"


def _camera_stream_plist_path(slot: Optional[int] = None) -> Path:
    """Path to the launchd plist. ``slot`` differentiates per-camera services."""
    label = _camera_stream_launchd_label(slot)
    return _user_home() / "Library" / "LaunchAgents" / f"{label}.plist"


def _camera_stream_log_path(slot: Optional[int] = None) -> Path:
    if slot is None or slot == 0:
        return _user_home() / ".cyberwave" / "camera_stream.log"
    return _user_home() / ".cyberwave" / f"camera_stream.{slot}.log"


def _camera_stream_launchd_label(slot: Optional[int] = None) -> str:
    if slot is None or slot == 0:
        return CAMERA_STREAM_LAUNCHD_LABEL
    return f"{CAMERA_STREAM_LAUNCHD_LABEL_PREFIX}{slot}"


def _camera_stream_port(slot: Optional[int] = None) -> int:
    """Port number for the ffmpeg MJPEG server of *slot*.

    Slot 0 keeps the legacy port (8091) for back-compat; additional slots
    are assigned consecutive ports above it.
    """
    if slot is None or slot == 0:
        return CAMERA_STREAM_PORT
    return CAMERA_STREAM_PORT + int(slot)


def _camera_streams_config_path() -> Path:
    return _user_home() / ".cyberwave" / CAMERA_STREAMS_FILENAME


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def _list_avfoundation_devices() -> list[tuple[int, str]]:
    """Return available AVFoundation video devices as ``[(index, name), ...]``.

    Parses the stderr output of ``ffmpeg -f avfoundation -list_devices true``.
    Returns an empty list on failure.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            text=True,
            timeout=10,
            env={
                **os.environ,
                "PATH": f"/opt/homebrew/bin:/usr/local/bin:{os.environ.get('PATH', '')}",
            },
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    devices: list[tuple[int, str]] = []
    in_video_section = False
    for line in result.stderr.splitlines():
        if "AVFoundation video devices:" in line:
            in_video_section = True
            continue
        if "AVFoundation audio devices:" in line:
            break
        if in_video_section:
            m = re.search(r"\[(\d+)] (.+)$", line)
            if m:
                devices.append((int(m.group(1)), m.group(2).strip()))
    return devices


def is_camera_stream_running() -> bool:
    """Check whether the MJPEG camera stream server is reachable."""
    return _is_port_listening(CAMERA_STREAM_PORT)


def _discover_camera_stream_slots() -> list[int]:
    """Return every launchd slot with an existing plist under ``~/Library/LaunchAgents``."""
    agents_dir = _user_home() / "Library" / "LaunchAgents"
    if not agents_dir.is_dir():
        return [0]
    slots: set[int] = {0}
    for entry in agents_dir.iterdir():
        name = entry.name
        if not name.startswith(CAMERA_STREAM_LAUNCHD_LABEL_PREFIX):
            continue
        if not name.endswith(".plist"):
            continue
        suffix = name[len(CAMERA_STREAM_LAUNCHD_LABEL_PREFIX) : -len(".plist")]
        try:
            slots.add(int(suffix))
        except ValueError:
            continue
    return sorted(slots)


def _teardown_camera_stream_server() -> None:
    """Stop every camera stream service and remove all related artifacts.

    Tears down both the legacy single-camera service (slot 0) and any
    per-camera services (slot ≥ 1) that were created for multi-twin
    mappings so a subsequent install starts from a clean slate.
    """
    console = _get_console()
    console.print("[cyan]Tearing down existing camera stream server(s)...[/cyan]")

    slots = _discover_camera_stream_slots()
    for slot in slots:
        _bootout_launchd_service(_camera_stream_launchd_label(slot))

    # Kill any lingering ffmpeg camera-stream processes that survived bootout.
    for _ in range(5):
        result = subprocess.run(
            ["pgrep", "-f", "ffmpeg.*avfoundation"],
            capture_output=True,
        )
        if result.returncode != 0:
            break
        subprocess.run(
            ["pkill", "-f", "ffmpeg.*avfoundation"],
            capture_output=True,
        )
        time.sleep(0.5)

    paths_to_remove: list[Path] = []
    for slot in slots:
        paths_to_remove.extend(
            [
                _camera_stream_plist_path(slot),
                _camera_stream_wrapper_path(slot),
                _camera_stream_log_path(slot),
            ]
        )
    paths_to_remove.append(_camera_streams_config_path())
    for path in paths_to_remove:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            console.print(f"[yellow]Could not remove {path}[/yellow]")

    console.print("[green]Camera stream server teardown complete.[/green]")


def _bring_up_camera_stream_slot(
    *, slot: int, device_name: str
) -> tuple[bool, bool]:
    """Materialize and start the ffmpeg launchd service for a single camera.

    Creates the per-slot wrapper + plist, reloads launchd, and waits for the
    MJPEG port to open.

    Returns ``(loaded, port_open)``:
      * ``loaded`` — the plist was successfully registered with launchd. When
        False, nothing else will bring this slot up and the caller should
        treat the slot as failed.
      * ``port_open`` — the MJPEG port was listening within the wait window.
        May be False when ``loaded`` is True (e.g. ffmpeg is still warming
        up, the camera is in use, or launchd will retry via ``KeepAlive``).
        Callers typically persist the mapping anyway so the service can
        recover transparently.
    """
    console = _get_console()

    wrapper_path = _camera_stream_wrapper_path(slot)
    plist_path = _camera_stream_plist_path(slot)
    log_path = _camera_stream_log_path(slot)
    label = _camera_stream_launchd_label(slot)
    port = _camera_stream_port(slot)

    wrapper_contents = _CAMERA_STREAM_WRAPPER_TEMPLATE.format(port=port)
    try:
        _write_file_as_real_user(wrapper_path, wrapper_contents, mode=0o755)
    except OSError as exc:
        console.print(f"[red]Failed to create camera stream script: {exc}[/red]")
        return False, False

    plist_contents = _CAMERA_STREAM_PLIST_TEMPLATE.format(
        label=label,
        wrapper_path=str(wrapper_path),
        log_path=str(log_path),
        device_name=_xml_escape(device_name),
    )
    try:
        _write_file_as_real_user(plist_path, plist_contents)
    except OSError as exc:
        console.print(f"[red]Failed to write camera stream plist: {exc}[/red]")
        return False, False

    console.print(f"[green]Created:[/green] {plist_path}")

    _, real_uid, _ = _resolve_real_user()
    gui_domain = f"gui/{real_uid}" if real_uid is not None else None

    wait_for_launchd_unload(label)

    if gui_domain:
        try:
            bootstrap_launchd_service(gui_domain, plist_path)
        except subprocess.CalledProcessError:
            console.print(
                "[yellow]launchctl bootstrap failed, falling back to load...[/yellow]"
            )
            try:
                _run(_launchctl_as_user(["load", str(plist_path)]))
            except subprocess.CalledProcessError as exc:
                console.print(
                    f"[red]Failed to load camera stream service "
                    f"(exit {exc.returncode}).[/red]"
                )
                return False, False
    else:
        try:
            _run(_launchctl_as_user(["load", str(plist_path)]))
        except subprocess.CalledProcessError as exc:
            console.print(
                f"[red]Failed to load camera stream service "
                f"(exit {exc.returncode}).[/red]"
            )
            return False, False

    max_wait_secs = 10
    for _ in range(max_wait_secs * 2):
        if _is_port_listening(port):
            console.print(
                f"[green]Camera stream server running on port {port} ({label}).[/green]"
            )
            return True, True
        time.sleep(0.5)

    console.print(
        f"[yellow]Camera stream service {label} loaded but port {port} is not "
        f"listening after {max_wait_secs}s. Check logs at {log_path}[/yellow]"
    )
    # launchd will keep retrying (``KeepAlive``); surface this to callers so
    # they can include the slot in a summary warning without tearing it down.
    return True, False


def _prompt_single_camera(
    cameras: list[tuple[int, str]], *, prompt_label: str, default_idx: int
) -> Optional[tuple[int, str]]:
    """Render a menu, read stdin, and return the chosen ``(index, name)`` or ``None``."""
    console = _get_console()
    console.print("[cyan]Available cameras:[/cyan]")
    valid_indices = {i for i, _ in cameras}
    for idx, name in cameras:
        console.print(f"  [bold]{idx}[/bold]) {name}")
    raw = input(f"{prompt_label} [{default_idx}]: ").strip()
    if raw == "":
        chosen_idx = default_idx
    else:
        try:
            chosen_idx = int(raw)
        except ValueError:
            console.print("[red]Invalid selection.[/red]")
            return None
    if chosen_idx not in valid_indices:
        console.print(f"[red]Camera index {chosen_idx} is not available.[/red]")
        return None
    name = next((n for i, n in cameras if i == chosen_idx), str(chosen_idx))
    return chosen_idx, name


def _persist_camera_streams_config(
    *,
    devices: list[tuple[int, str]],
    twin_to_stream_url: dict[str, str],
) -> None:
    """Write ``camera_streams.json`` so edge-core can resolve per-twin URLs."""
    import json

    console = _get_console()
    path = _camera_streams_config_path()
    data = {
        "devices": [{"index": idx, "name": name} for idx, name in devices],
        "twin_to_stream_url": twin_to_stream_url,
    }
    try:
        _write_file_as_real_user(path, json.dumps(data, indent=2))
        console.print(f"[dim]Saved twin→camera map to {path}[/dim]")
    except OSError as exc:
        console.print(
            f"[yellow]Could not persist {path}: {exc}[/yellow]\n"
            f"[dim]Per-twin camera mapping may not survive reboots.[/dim]"
        )


def setup_camera_stream_server(
    *,
    force: bool = False,
    device_index: Optional[int] = None,
    camera_twins: Optional[list[tuple[str, str]]] = None,
) -> bool:
    """Install the optional MJPEG camera stream server on macOS.

    This is a fallback for when USB/IP bandwidth is insufficient for
    video cameras.  It uses ffmpeg to capture from the macOS camera and
    serves an HTTP MJPEG stream that Docker containers consume.

    When *force* is True, the existing installation is torn down first.
    *device_index* selects the AVFoundation camera; when ``None``, the
    user is prompted interactively (or ``0`` is used when only one camera
    is available).

    When *camera_twins* lists two or more camera-bearing twins **and** the
    host exposes at least two AVFoundation devices, the user is walked
    through a per-twin mapping and one ffmpeg launchd service is started
    per distinct physical camera (on sequential ports starting at
    :data:`CAMERA_STREAM_PORT`).  The mapping is persisted to
    ``~/.cyberwave/camera_streams.json`` so edge-core can bind each driver
    container to the correct MJPEG endpoint.

    Returns True on success.  Returns True immediately on non-macOS.
    """
    if not is_macos():
        return True

    console = _get_console()

    if force:
        _teardown_camera_stream_server()
    elif is_camera_stream_running():
        console.print("[green]Camera stream server is already running.[/green]")
        return True

    if not _has_ffmpeg():
        console.print(
            "[red]ffmpeg is required for the camera stream server.[/red]\n"
            "[dim]Install with: brew install ffmpeg[/dim]"
        )
        return False

    console.print(
        "\n[bold]Camera Stream Server Setup[/bold]\n"
        "This creates an MJPEG stream from your macOS camera that Docker\n"
        "containers can consume.\n"
    )

    camera_twins = camera_twins or []
    cameras = _list_avfoundation_devices() if device_index is None else []
    multi_mapping = (
        device_index is None
        and len(camera_twins) >= 2
        and len(cameras) >= 2
    )

    if multi_mapping:
        return _setup_camera_stream_server_multi(cameras, camera_twins)

    # --- legacy single-camera flow ---
    # AVFoundation numeric indices are unstable (they shift when devices
    # connect/disconnect), so we resolve and store the *device name* which
    # ffmpeg also accepts via ``-i``.
    device_name: Optional[str] = None
    if device_index is None:
        if not cameras:
            console.print(
                "[yellow]Could not detect AVFoundation cameras; defaulting to device 0.[/yellow]"
            )
            device_name = "0"
        elif len(cameras) == 1:
            device_name = cameras[0][1]
            console.print(f"[cyan]Detected camera:[/cyan] {device_name}")
        else:
            chosen = _prompt_single_camera(
                cameras,
                prompt_label="Enter camera number",
                default_idx=cameras[0][0],
            )
            if chosen is None:
                return False
            _, device_name = chosen
            console.print(f"[green]Selected:[/green] {device_name}")
    else:
        device_name = str(device_index)

    loaded, _port_open = _bring_up_camera_stream_slot(slot=0, device_name=device_name)
    if not loaded:
        return False

    stream_url = f"http://host.docker.internal:{CAMERA_STREAM_PORT}"
    try:
        from .credentials import upsert_runtime_env

        upsert_runtime_env("CYBERWAVE_MACOS_CAMERA_STREAM_URL", stream_url)
        console.print(
            f"[green]Saved[/green] CYBERWAVE_MACOS_CAMERA_STREAM_URL={stream_url} "
            "to credentials.json"
        )
    except Exception as exc:
        console.print(
            f"[yellow]Could not persist camera stream URL: {exc}[/yellow]\n"
            f"[dim]Set manually: export CYBERWAVE_MACOS_CAMERA_STREAM_URL={stream_url}[/dim]"
        )

    # When we already know which twins want this single stream, record a 1-1
    # mapping so edge-core uses the same code path as the multi-camera case.
    if camera_twins:
        twin_map = {
            str(twin_uuid): stream_url for twin_uuid, _ in camera_twins
        }
        _persist_camera_streams_config(
            devices=cameras or [(0, device_name or "0")],
            twin_to_stream_url=twin_map,
        )

    return True


def _setup_camera_stream_server_multi(
    cameras: list[tuple[int, str]],
    camera_twins: list[tuple[str, str]],
) -> bool:
    """Per-twin mapping path: one ffmpeg service per distinct physical camera.

    The prompt loop persists partial progress: if the user aborts with Ctrl-C
    or enters an invalid value, the twins that were already mapped are kept
    and the remaining ones fall back to whatever edge-core's legacy resolution
    picks.  After bringing up each distinct camera's ffmpeg service, the
    function surfaces a per-slot summary so silent port-binding failures
    don't hide behind a green "install complete" message.
    """
    console = _get_console()

    console.print(
        f"\n[bold]Detected {len(cameras)} camera(s) and "
        f"{len(camera_twins)} camera twin(s).[/bold]"
    )
    console.print(
        "[dim]Map each twin to the physical camera it is wired to. "
        "A camera may be shared across twins.[/dim]\n"
    )

    # twin_uuid -> (camera_index, camera_name)
    twin_assignments: dict[str, tuple[int, str]] = {}
    # camera_index -> (camera_name, slot)
    camera_to_slot: dict[int, tuple[str, int]] = {}

    valid_indices = {i for i, _ in cameras}
    default_idx = cameras[0][0]

    twin_name_by_uuid = dict(camera_twins)

    aborted = False
    for twin_uuid, twin_name in camera_twins:
        console.print(f"[bold]Twin:[/bold] {twin_name} [dim]({twin_uuid[:8]}...)[/dim]")
        for idx, name in cameras:
            assigned_names = [
                twin_name_by_uuid.get(other_uuid, other_uuid)
                for other_uuid, (other_idx, _) in twin_assignments.items()
                if other_idx == idx
            ]
            suffix = (
                f"  [dim](already assigned to: {', '.join(assigned_names)})[/dim]"
                if assigned_names
                else ""
            )
            console.print(f"  [bold]{idx}[/bold]) {name}{suffix}")
        try:
            raw = input(f"Camera for {twin_name} [{default_idx}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print(
                "\n[yellow]Aborted — keeping mappings made so far; "
                "remaining twins will fall back to the default stream.[/yellow]"
            )
            aborted = True
            break
        if raw == "":
            chosen_idx = default_idx
        else:
            try:
                chosen_idx = int(raw)
            except ValueError:
                console.print(
                    "[yellow]Invalid selection — keeping mappings made so far; "
                    "remaining twins will fall back to the default stream.[/yellow]"
                )
                aborted = True
                break
        if chosen_idx not in valid_indices:
            console.print(
                f"[yellow]Camera index {chosen_idx} is not available — "
                f"keeping mappings made so far; remaining twins will fall back "
                f"to the default stream.[/yellow]"
            )
            aborted = True
            break
        chosen_name = next((n for i, n in cameras if i == chosen_idx), str(chosen_idx))
        twin_assignments[twin_uuid] = (chosen_idx, chosen_name)
        if chosen_idx not in camera_to_slot:
            camera_to_slot[chosen_idx] = (chosen_name, len(camera_to_slot))
            # Shift default toward an unmapped camera to speed up the flow.
            remaining = [i for i in valid_indices if i not in camera_to_slot]
            if remaining:
                default_idx = remaining[0]
        console.print(f"[green]Selected:[/green] {chosen_name}\n")

    if not twin_assignments:
        console.print("[yellow]No twins mapped — skipping camera stream setup.[/yellow]")
        return False

    # Bring up one ffmpeg service per distinct camera and track per-slot state.
    #   slot -> (camera_name, port_open)
    slot_status: dict[int, tuple[str, bool]] = {}
    for camera_idx, (camera_name, slot) in camera_to_slot.items():
        loaded, port_open = _bring_up_camera_stream_slot(
            slot=slot, device_name=camera_name
        )
        if not loaded:
            console.print(
                f"[red]Failed to register ffmpeg service for {camera_name} "
                f"(slot {slot}) — skipping.[/red]"
            )
            continue
        slot_status[slot] = (camera_name, port_open)

    # Build twin_uuid -> stream URL map for every successfully *registered*
    # slot.  Port-open failures are persisted too so launchd's KeepAlive can
    # recover the service later; they're surfaced via the summary below.
    twin_to_stream_url: dict[str, str] = {}
    for twin_uuid, (camera_idx, _) in twin_assignments.items():
        slot = camera_to_slot[camera_idx][1]
        if slot not in slot_status:
            continue
        port = _camera_stream_port(slot)
        twin_to_stream_url[str(twin_uuid)] = f"http://host.docker.internal:{port}"

    _persist_camera_streams_config(
        devices=cameras,
        twin_to_stream_url=twin_to_stream_url,
    )

    # Set the legacy env var to the first mapped twin's URL for back-compat
    # with older edge-core builds that only know about the single stream URL.
    if twin_to_stream_url:
        first_mapped_uuid = next(
            (str(uuid) for uuid, _ in camera_twins if str(uuid) in twin_to_stream_url),
            None,
        )
        if first_mapped_uuid is not None:
            try:
                from .credentials import upsert_runtime_env

                primary_url = twin_to_stream_url[first_mapped_uuid]
                upsert_runtime_env("CYBERWAVE_MACOS_CAMERA_STREAM_URL", primary_url)
                console.print(
                    f"[green]Saved[/green] CYBERWAVE_MACOS_CAMERA_STREAM_URL={primary_url} "
                    "to credentials.json (first mapped twin's stream)"
                )
            except Exception as exc:
                console.print(
                    f"[yellow]Could not persist primary camera stream URL: {exc}[/yellow]"
                )

    _print_multi_camera_summary(
        camera_twins=camera_twins,
        twin_assignments=twin_assignments,
        camera_to_slot=camera_to_slot,
        slot_status=slot_status,
        aborted=aborted,
    )

    # Success means at least one twin has a live mapping.  If every slot
    # failed to register, signal failure to the caller.
    return bool(twin_to_stream_url)


def _print_multi_camera_summary(
    *,
    camera_twins: list[tuple[str, str]],
    twin_assignments: dict[str, tuple[int, str]],
    camera_to_slot: dict[int, tuple[str, int]],
    slot_status: dict[int, tuple[str, bool]],
    aborted: bool,
) -> None:
    """Render the final per-twin / per-slot result table.

    Makes it obvious which twins were mapped, which were skipped, and which
    physical cameras have a running MJPEG server vs. a pending one.
    """
    console = _get_console()
    console.print("\n[bold]Camera stream setup summary[/bold]")

    unmapped_names: list[str] = []
    for twin_uuid, twin_name in camera_twins:
        assignment = twin_assignments.get(twin_uuid)
        if assignment is None:
            unmapped_names.append(twin_name)
            continue
        _, camera_name = assignment
        slot = camera_to_slot[assignment[0]][1]
        status = slot_status.get(slot)
        if status is None:
            console.print(
                f"  [red]✗[/red] {twin_name} → {camera_name} "
                f"[red](service failed to register)[/red]"
            )
        elif status[1]:
            console.print(
                f"  [green]✓[/green] {twin_name} → {camera_name} "
                f"[dim](slot {slot}, port {_camera_stream_port(slot)})[/dim]"
            )
        else:
            console.print(
                f"  [yellow]○[/yellow] {twin_name} → {camera_name} "
                f"[yellow](slot {slot}, port {_camera_stream_port(slot)} not "
                f"yet listening — launchd will retry)[/yellow]"
            )

    if unmapped_names:
        console.print(
            "  [dim]Unmapped twins will use the default stream: "
            + ", ".join(unmapped_names)
            + "[/dim]"
        )

    pending = [s for s, (_, ok) in slot_status.items() if not ok]
    if pending:
        console.print(
            f"[yellow]Note:[/yellow] {len(pending)} stream(s) did not become "
            f"reachable during setup.  If a twin keeps failing, re-run "
            f"[bold]cyberwave edge install --reconfigure-camera[/bold] after "
            f"checking the ffmpeg logs under ~/.cyberwave/."
        )
    if aborted:
        console.print(
            "[dim]Mapping was stopped before every twin was walked; "
            "run the reconfigure flow above to finish it.[/dim]"
        )


# ---- Edge-core launchd service -----------------------------------------------
# LaunchAgent for cyberwave-edge-core, giving macOS the same
# start/stop/restart/status/logs experience as systemd on Linux.

EDGE_CORE_LAUNCHD_LABEL = "com.cyberwave.edge.core"
# Legacy label used by earlier CLI builds; tracked so teardown / bootout sweeps
# still clean it up on machines installed before the labels were unified.
_LEGACY_EDGE_CORE_LAUNCHD_LABELS: tuple[str, ...] = ("com.cyberwave.edge-core",)


def legacy_labels_for_package(package_name: str) -> tuple[str, ...]:
    """Return historical launchd labels that should be cleaned up before
    bootstrapping a fresh install of *package_name*.

    Older CLI versions used different label conventions; this lets the
    install path quietly migrate users without leaving zombie services
    behind under stale labels.
    """
    if package_name == "cyberwave-edge-core":
        return _LEGACY_EDGE_CORE_LAUNCHD_LABELS
    return ()

_EDGE_CORE_WRAPPER_TEMPLATE = textwrap.dedent("""\
    #!/bin/bash
    # Cyberwave edge-core — launched by launchd as a background service.
    # launchd uses a minimal PATH; include Homebrew and Docker paths.
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
    exec {python_path} -m cyberwave_edge_core.main
""")

_EDGE_CORE_PLIST_TEMPLATE = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
      "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>Label</key>
        <string>{label}</string>
        <key>ProgramArguments</key>
        <array>
            <string>{wrapper_path}</string>
        </array>
        <key>RunAtLoad</key>
        <true/>
        <key>KeepAlive</key>
        <true/>
        <key>StandardOutPath</key>
        <string>{log_path}</string>
        <key>StandardErrorPath</key>
        <string>{log_path}</string>
    </dict>
    </plist>
""")


def _edge_core_wrapper_path() -> Path:
    return _user_home() / ".cyberwave" / "edge_core.sh"


def edge_core_plist_path() -> Path:
    return (
        _user_home()
        / "Library"
        / "LaunchAgents"
        / f"{EDGE_CORE_LAUNCHD_LABEL}.plist"
    )


def edge_core_log_path() -> Path:
    return _user_home() / "Library" / "Logs" / "cyberwave" / "edge-core.log"


def is_edge_core_service_loaded() -> bool:
    """Return True when the edge-core LaunchAgent is loaded in launchd."""
    _, real_uid, _ = _resolve_real_user()
    uid = real_uid if real_uid is not None else os.getuid()
    try:
        result = subprocess.run(
            _launchctl_as_user(["print", f"gui/{uid}/{EDGE_CORE_LAUNCHD_LABEL}"]),
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except OSError:
        return False


def is_edge_core_service_running() -> bool:
    """Return True when edge-core's launchd job has a running PID."""
    _, real_uid, _ = _resolve_real_user()
    uid = real_uid if real_uid is not None else os.getuid()
    try:
        result = subprocess.run(
            _launchctl_as_user(["print", f"gui/{uid}/{EDGE_CORE_LAUNCHD_LABEL}"]),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("pid = ") or stripped.startswith("pid =\t"):
                pid_val = stripped.split("=", 1)[1].strip()
                return pid_val.isdigit() and int(pid_val) > 0
        return False
    except OSError:
        return False


def teardown_edge_core_launchd_service() -> None:
    """Stop the edge-core LaunchAgent and remove all related artifacts."""
    console = _get_console()
    console.print("[cyan]Tearing down edge-core launchd service...[/cyan]")

    _bootout_launchd_service(EDGE_CORE_LAUNCHD_LABEL)
    for legacy_label in _LEGACY_EDGE_CORE_LAUNCHD_LABELS:
        _bootout_launchd_service(legacy_label)

    launch_agents_dir = _user_home() / "Library" / "LaunchAgents"
    legacy_plist_paths = [
        launch_agents_dir / f"{label}.plist"
        for label in _LEGACY_EDGE_CORE_LAUNCHD_LABELS
    ]

    for path in [
        edge_core_plist_path(),
        _edge_core_wrapper_path(),
        *legacy_plist_paths,
    ]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            console.print(f"[yellow]Could not remove {path}[/yellow]")

    console.print("[green]Edge-core launchd service teardown complete.[/green]")


def start_edge_core_service() -> bool:
    """Start (or restart) the edge-core LaunchAgent."""
    console = _get_console()
    _, real_uid, _ = _resolve_real_user()
    uid = real_uid if real_uid is not None else os.getuid()

    if not edge_core_plist_path().exists():
        console.print(
            "[red]Edge-core LaunchAgent not installed. "
            "Run 'cyberwave edge install' first.[/red]"
        )
        return False

    if is_edge_core_service_loaded():
        try:
            _run(
                _launchctl_as_user(
                    ["kickstart", "-k", f"gui/{uid}/{EDGE_CORE_LAUNCHD_LABEL}"]
                ),
                check=True,
            )
            console.print("[green]Edge-core service restarted.[/green]")
            return True
        except subprocess.CalledProcessError:
            pass

    gui_domain = f"gui/{uid}"
    wait_for_launchd_unload(
        EDGE_CORE_LAUNCHD_LABEL,
        legacy_labels=_LEGACY_EDGE_CORE_LAUNCHD_LABELS,
    )
    try:
        bootstrap_launchd_service(gui_domain, edge_core_plist_path())
    except subprocess.CalledProcessError:
        try:
            _run(
                _launchctl_as_user(["load", str(edge_core_plist_path())]),
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            console.print(
                f"[red]Failed to start edge-core service (exit {exc.returncode}).[/red]"
            )
            return False

    console.print("[green]Edge-core service started.[/green]")
    return True


def stop_edge_core_service() -> bool:
    """Stop the edge-core LaunchAgent."""
    console = _get_console()
    if not is_edge_core_service_loaded():
        console.print("[yellow]Edge-core service is not running.[/yellow]")
        return True

    _bootout_launchd_service(EDGE_CORE_LAUNCHD_LABEL)
    console.print("[green]Edge-core service stopped.[/green]")
    return True


def setup_edge_core_launchd_service(*, force: bool = False) -> bool:
    """Install and start the edge-core LaunchAgent on macOS.

    Creates a wrapper script (with the absolute Python path baked in)
    and a launchd plist, then bootstraps the service.

    Returns True on success.  Returns True immediately on non-macOS.
    """
    if not is_macos():
        return True

    console = _get_console()

    if force:
        teardown_edge_core_launchd_service()
    elif is_edge_core_service_running():
        console.print("[green]Edge-core service is already running.[/green]")
        return True

    wrapper_path = _edge_core_wrapper_path()
    plist_path = edge_core_plist_path()
    log_path = edge_core_log_path()

    # Ensure the log directory exists (launchd won't create it).
    try:
        _write_file_as_real_user(log_path, "", mode=0o644)
    except OSError:
        pass

    python_path = sys.executable
    wrapper_contents = _EDGE_CORE_WRAPPER_TEMPLATE.format(python_path=python_path)
    try:
        _write_file_as_real_user(wrapper_path, wrapper_contents, mode=0o755)
    except OSError as exc:
        console.print(f"[red]Failed to create edge-core wrapper script: {exc}[/red]")
        return False

    plist_contents = _EDGE_CORE_PLIST_TEMPLATE.format(
        label=EDGE_CORE_LAUNCHD_LABEL,
        wrapper_path=str(wrapper_path),
        log_path=str(log_path),
    )
    try:
        _write_file_as_real_user(plist_path, plist_contents)
    except OSError as exc:
        console.print(f"[red]Failed to create edge-core plist: {exc}[/red]")
        return False

    console.print(f"Created: {plist_path}")

    wait_for_launchd_unload(
        EDGE_CORE_LAUNCHD_LABEL,
        legacy_labels=_LEGACY_EDGE_CORE_LAUNCHD_LABELS,
    )

    _, real_uid, _ = _resolve_real_user()
    uid = real_uid if real_uid is not None else os.getuid()
    gui_domain = f"gui/{uid}"

    try:
        bootstrap_launchd_service(gui_domain, plist_path)
    except subprocess.CalledProcessError:
        console.print(
            "[yellow]launchctl bootstrap failed, falling back to load...[/yellow]"
        )
        try:
            _run(_launchctl_as_user(["load", str(plist_path)]))
        except subprocess.CalledProcessError as exc:
            console.print(
                f"[red]Failed to load edge-core service (exit {exc.returncode}).[/red]"
            )
            return False

    max_wait_secs = 5
    for _ in range(max_wait_secs * 2):
        if is_edge_core_service_running():
            console.print(
                f"[green]Edge-core service running ({EDGE_CORE_LAUNCHD_LABEL}).[/green]"
            )
            break
        time.sleep(0.5)
    else:
        console.print(
            f"[yellow]Edge-core service loaded but process not detected after "
            f"{max_wait_secs}s. Check logs: {log_path}[/yellow]"
        )

    return True
