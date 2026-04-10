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
            return ["sudo", "-u", username, "launchctl", *args]
    return ["launchctl", *args]


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
    """Best-effort chown to the invoking user (relevant when running under sudo)."""
    _, uid, gid = _resolve_real_user()
    if uid is None:
        return
    try:
        os.chown(path, uid, gid or -1)
        if recursive and path.is_dir():
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

    _bootout_launchd_service(USBIP_LAUNCHD_LABEL)

    if gui_domain:
        try:
            _run(_launchctl_as_user(["bootstrap", gui_domain, str(plist_path)]))
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

_CAMERA_STREAM_WRAPPER_TEMPLATE = textwrap.dedent("""\
    #!/bin/bash
    # Cyberwave camera stream — captures from macOS camera and serves MJPEG.
    # launchd uses a minimal PATH; ensure Homebrew paths are included.
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
    DEVICE="${{CYBERWAVE_CAMERA_DEVICE:-0}}"
    PORT="${{CYBERWAVE_CAMERA_STREAM_PORT:-{port}}}"
    RESOLUTION="${{CYBERWAVE_CAMERA_STREAM_RESOLUTION:-1280x720}}"
    FPS="${{CYBERWAVE_CAMERA_STREAM_FPS:-30}}"

    exec ffmpeg -hide_banner -loglevel warning \\
        -fflags nobuffer -flags low_delay -avioflags direct \\
        -f avfoundation -framerate "$FPS" -video_size "$RESOLUTION" \\
        -thread_queue_size 1 -i "$DEVICE" \\
        -c:v mjpeg -q:v 5 \\
        -fflags nobuffer -flush_packets 1 \\
        -f mjpeg \\
        -listen 1 \\
        "http://0.0.0.0:$PORT"
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


def _camera_stream_wrapper_path() -> Path:
    return _user_home() / ".cyberwave" / "camera_stream.sh"


def _camera_stream_plist_path() -> Path:
    return _user_home() / "Library" / "LaunchAgents" / f"{CAMERA_STREAM_LAUNCHD_LABEL}.plist"


def _camera_stream_log_path() -> Path:
    return _user_home() / ".cyberwave" / "camera_stream.log"


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


def _teardown_camera_stream_server() -> None:
    """Stop the camera stream server and remove all related artifacts."""
    console = _get_console()
    console.print("[cyan]Tearing down existing camera stream server...[/cyan]")

    _bootout_launchd_service(CAMERA_STREAM_LAUNCHD_LABEL)

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

    for path in [
        _camera_stream_plist_path(),
        _camera_stream_wrapper_path(),
        _camera_stream_log_path(),
    ]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            console.print(f"[yellow]Could not remove {path}[/yellow]")

    console.print("[green]Camera stream server teardown complete.[/green]")


def setup_camera_stream_server(
    *,
    force: bool = False,
    device_index: Optional[int] = None,
) -> bool:
    """Install the optional MJPEG camera stream server on macOS.

    This is a fallback for when USB/IP bandwidth is insufficient for
    video cameras.  It uses ffmpeg to capture from the macOS camera and
    serves an HTTP MJPEG stream that Docker containers consume.

    When *force* is True, the existing installation is torn down first.
    *device_index* selects the AVFoundation camera; when ``None``, the
    user is prompted interactively (or ``0`` is used when only one camera
    is available).

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

    # --- camera device selection ---
    # AVFoundation numeric indices are unstable (they shift when devices
    # connect/disconnect), so we resolve and store the *device name* which
    # ffmpeg also accepts via ``-i``.
    device_name: Optional[str] = None
    if device_index is None:
        cameras = _list_avfoundation_devices()
        if not cameras:
            console.print(
                "[yellow]Could not detect AVFoundation cameras; defaulting to device 0.[/yellow]"
            )
            device_name = "0"
        elif len(cameras) == 1:
            device_name = cameras[0][1]
            console.print(f"[cyan]Detected camera:[/cyan] {device_name}")
        else:
            console.print("[cyan]Available cameras:[/cyan]")
            for idx, name in cameras:
                console.print(f"  [bold]{idx}[/bold]) {name}")
            valid_indices = {i for i, _ in cameras}
            raw = input(f"Enter camera number [{cameras[0][0]}]: ").strip()
            if raw == "":
                chosen_idx = cameras[0][0]
            else:
                try:
                    chosen_idx = int(raw)
                except ValueError:
                    console.print("[red]Invalid selection.[/red]")
                    return False
            if chosen_idx not in valid_indices:
                console.print(
                    f"[red]Camera index {chosen_idx} is not available.[/red]"
                )
                return False
            device_name = next(
                (n for i, n in cameras if i == chosen_idx), str(chosen_idx)
            )
            console.print(f"[green]Selected:[/green] {device_name}")
    else:
        device_name = str(device_index)

    wrapper_path = _camera_stream_wrapper_path()
    plist_path = _camera_stream_plist_path()
    log_path = _camera_stream_log_path()

    wrapper_contents = _CAMERA_STREAM_WRAPPER_TEMPLATE.format(port=CAMERA_STREAM_PORT)
    try:
        _write_file_as_real_user(wrapper_path, wrapper_contents, mode=0o755)
    except OSError as exc:
        console.print(f"[red]Failed to create camera stream script: {exc}[/red]")
        return False

    plist_contents = _CAMERA_STREAM_PLIST_TEMPLATE.format(
        label=CAMERA_STREAM_LAUNCHD_LABEL,
        wrapper_path=str(wrapper_path),
        log_path=str(log_path),
        device_name=_xml_escape(device_name),
    )

    try:
        _write_file_as_real_user(plist_path, plist_contents)
    except OSError as exc:
        console.print(f"[red]Failed to write camera stream plist: {exc}[/red]")
        return False

    console.print(f"[green]Created:[/green] {plist_path}")

    _, real_uid, _ = _resolve_real_user()
    gui_domain = f"gui/{real_uid}" if real_uid is not None else None

    _bootout_launchd_service(CAMERA_STREAM_LAUNCHD_LABEL)

    if gui_domain:
        try:
            _run(_launchctl_as_user(["bootstrap", gui_domain, str(plist_path)]))
        except subprocess.CalledProcessError:
            console.print("[yellow]launchctl bootstrap failed, falling back to load...[/yellow]")
            try:
                _run(_launchctl_as_user(["load", str(plist_path)]))
            except subprocess.CalledProcessError as exc:
                console.print(
                    f"[red]Failed to load camera stream service (exit {exc.returncode}).[/red]"
                )
                return False
    else:
        try:
            _run(_launchctl_as_user(["load", str(plist_path)]))
        except subprocess.CalledProcessError as exc:
            console.print(
                f"[red]Failed to load camera stream service (exit {exc.returncode}).[/red]"
            )
            return False

    max_wait_secs = 10
    for _ in range(max_wait_secs * 2):
        if is_camera_stream_running():
            console.print(
                f"[green]Camera stream server running on port {CAMERA_STREAM_PORT} "
                f"({CAMERA_STREAM_LAUNCHD_LABEL}).[/green]"
            )
            break
        time.sleep(0.5)
    else:
        console.print(
            f"[yellow]Camera stream service loaded but port {CAMERA_STREAM_PORT} is not "
            f"listening after {max_wait_secs}s. Check logs at {log_path}[/yellow]"
        )

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

    return True


# ---- Edge-core launchd service -----------------------------------------------
# LaunchAgent for cyberwave-edge-core, giving macOS the same
# start/stop/restart/status/logs experience as systemd on Linux.

EDGE_CORE_LAUNCHD_LABEL = "com.cyberwave.edge-core"

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

    for path in [
        edge_core_plist_path(),
        _edge_core_wrapper_path(),
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
    try:
        _run(
            _launchctl_as_user(["bootstrap", gui_domain, str(edge_core_plist_path())]),
            check=True,
        )
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

    _bootout_launchd_service(EDGE_CORE_LAUNCHD_LABEL)

    _, real_uid, _ = _resolve_real_user()
    uid = real_uid if real_uid is not None else os.getuid()
    gui_domain = f"gui/{uid}"

    try:
        _run(_launchctl_as_user(["bootstrap", gui_domain, str(plist_path)]))
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
