# Diagnosing edge camera and microphone discovery

## What the testbed evidence tells us

The October 2026 testbed report contains two separate problems. Camera discovery reports that `/dev/video0` cannot be opened, while twin downloads fail because three paths ending in `.json` are directories. Neither failure proves that Edge Core failed to install, and a running systemd service does not prove that every driver started successfully.

For the camera problem, the decisive observations are the absence of `/dev/video*`, the absence of any USB Video interface in sysfs, and an `lsusb` inventory containing hubs, Bluetooth, a display adapter and a Logitech receiver, but no RealSense camera. The `uvcvideo` module is already loaded. Its usage count of zero is consistent with the other evidence, but is not by itself a reliable camera inventory. The log line registering the UVC driver means the driver registered, not that it found a camera.

This points first to physical connection, power, a data cable, USB routing or virtualization passthrough. We cannot choose between those causes from the transcript. The virtualization command was misspelled, so the report has not established whether this is a VM. The presence of an `ov13858` module also does not establish that an integrated MIPI camera has a functioning capture pipeline. USB checks apply to USB cameras; IP cameras such as the AX8 need network discovery and configuration instead. Selecting a twin in Cyberwave does not make its hardware locally attached.

The referenced screenshot files were not readable in this session. This diagnosis uses the later pasted command output, not an interpretation of those images.

## Recover the other machine without a new CLI

Start on the host where Edge Core actually runs:

```bash
systemd-detect-virt
lsusb
lsusb -t
lsmod | grep uvcvideo
ls -l /dev/video* /dev/v4l/by-id/* /dev/v4l/by-path/*
grep -l '^0e$' /sys/bus/usb/devices/*/bInterfaceClass
sudo dmesg -w
```

Leave the last command running while unplugging and reconnecting one expected USB camera, then stop it with Ctrl-C. Use a known working data cable and a direct USB port; avoid the hub initially and check the camera's power requirements. A RealSense should appear in `lsusb` before the CLI can do anything useful with it. If it still does not, try the camera and cable on another host. In a VM, arrange USB passthrough from the physical host and repeat the checks inside the guest. A container also needs device exposure; `systemd-detect-virt` can report containers as well as VMs. `none` means no virtualization was detected, not a guarantee about all hardware routing.

When USB sees the camera but no video node appears, inspect the new kernel messages for probe, firmware, bandwidth or USB errors. `sudo modprobe uvcvideo` is appropriate for an unbound UVC device, but repeatedly loading an already loaded driver does not repair a missing USB connection. Install kernel modules matching the running kernel using that distribution's packages only if the module is actually missing. An Ubuntu `linux-modules-extra` recipe is not portable to every kernel or Raspberry Pi OS.

Once nodes appear, use a real node rather than assuming index zero:

```bash
for node in /dev/video*; do
    [ -c "$node" ] || continue
    v4l2-ctl -d "$node" --list-devices
    v4l2-ctl -d "$node" --all
    break
done
cyberwave edge cameras
```

Inspect the reported capabilities and formats of the actual capture node with `v4l2-ctl -d /dev/videoN --list-formats-ext`, replacing `N` with the discovered index. Some nodes carry metadata rather than frames. Do not create a fake `/dev/video0` or rename nodes to hide the error. After hardware discovery works, rerun `sudo cyberwave edge install` when twin assignments need configuration; `edge cameras` only lists devices, and `--save` saves an inventory rather than interactively assigning twins.

Adding the operator to `video` only helps when device nodes exist and their permissions grant that group access. The supplied `sudo usermod -aG video "$USER"` and `newgrp video` are reasonable for that case; `newgrp` affects its new shell, while existing processes retain their groups. Logging out and back in refreshes the normal session. It cannot solve absent hardware, and the reported installation already ran under sudo.

The command names are `lsusb`, `systemd-detect-virt`, and `v4l2-ctl --version`. `lsub`, `system-detect-virt`, and `--verison` are spelling errors, not evidence of older libraries. The CLI does not invoke an `lsub` program. `grep dmesg` waits for standard input; use `sudo dmesg | grep -iE 'uvc|usb' | tail -30` instead.

## Versions and updates

First identify the executable that each context resolves. Sudo can select a different installation from the user's shell.

```bash
type -a cyberwave
cyberwave --version
sudo sh -c 'command -v cyberwave; cyberwave --version'
v4l2-ctl --version
dpkg-query -W -f='${Package} ${Version}\n' v4l-utils 'cyberwave-cli*' 'cyberwave-edge-core*'
systemctl show cyberwave-edge-core.service -p ExecStart
systemctl status cyberwave-edge-core.service --no-pager
```

The Debian package version identifies the installed Edge Core package. Check `ExecStart` as well: a custom service can run a different executable. With a recent CLI, `sudo cyberwave edge update --check` reports the installed and available Edge Core versions and refreshes the package index.

For an APT-installed CLI on Ubuntu or Debian with the Cyberwave repository already configured:

```bash
sudo apt-get update
sudo apt-get install --only-upgrade cyberwave-cli
hash -r
cyberwave --version
sudo cyberwave edge update --check
sudo cyberwave edge update
sudo journalctl -u cyberwave-edge-core.service -n 100 --no-pager
```

`edge update` updates the installed channel and restarts the service when needed. It does not automatically move a dev or staging installation to stable. If the old CLI lacks this command, update the installed package directly; for the stable package this is:

```bash
sudo apt-get update
sudo apt-get install --only-upgrade cyberwave-edge-core
sudo systemctl restart cyberwave-edge-core.service
```

For other channels substitute the installed name (`cyberwave-edge-core-dev`, `cyberwave-edge-core-staging`, or `cyberwave-edge-core-pr`). PR builds require an explicit version with the current CLI. Inspect `apt-cache policy PACKAGE` and `apt-mark showhold` if an upgrade is not offered. These commands obtain the latest candidate in the configured repository/channel, not unreleased local changes.

If the CLI was installed with pipx, use `pipx upgrade cyberwave-cli`; for uv tools use `uv tool upgrade cyberwave-cli`; for a virtual environment run its Python with `-m pip install --upgrade cyberwave-cli`. Update the installation identified by `type -a`, rather than installing a second copy and assuming sudo will use it. The documented installer is `curl -fsSL https://cyberwave.com/install.sh | bash` when the repository is not configured. Verify executable resolution again afterward.

## Why an older v4l2-ctl can report video0 even when discovery is useful

The upstream [1.22.1 source](https://github.com/gjasny/v4l-utils/blob/v4l-utils-1.22.1/utils/v4l2-ctl/v4l2-ctl.cpp) defaults to `/dev/video0` and continues into device inspection after command parsing. Listing output can therefore precede an error for the default node. The upstream [1.30.0 source](https://github.com/gjasny/v4l-utils/blob/v4l-utils-1.30.0/utils/v4l2-ctl/v4l2-ctl.cpp) has an early return through `common_list_devices` before that inspection. This confirms the old/new behavior, but does not establish that every release below 1.30 or every distributor build behaves identically. Backports can change behavior. The implementation deliberately does not branch on a version number.

`1.2`, `1.22.1`, and `1.30` are different versions, not decimal numbers. A typo rejected by `--verison` tells us nothing about the installed version. Debian Bookworm's [package listing](https://packages.debian.org/bookworm/v4l-utils) identifies the 1.22.1 series; inspect `dpkg-query` on Ubuntu and Raspberry Pi OS rather than deriving an exact version from their distribution names. Upgrading this utility can remove a misleading error, but cannot make an absent USB device appear.

## CLI changes and compatibility

The existing discovery workaround remains: skip the subprocess when there are no video nodes, pass an actual discovered node with `-d`, and retain usable stdout when the tool returns nonzero. The node enumeration now accepts numeric character-device nodes and sorts by index; there is no special dependency on video0 or video2, CPU architecture or a camera model. Hot-unplug and permission failures remain possible and should still be reported.

Missing-tool hints again choose apt-get, pacman or a generic package-manager instruction. A UVC interface without a bound driver is described as unbound, rather than claiming the module is definitely unloaded. If nodes exist but no camera is returned, the hint allows for discovery failure as well as codec/ISP nodes. Linux probing stays separate from macOS AVFoundation; Windows discovery remains explicitly unsupported.

The `write_cameras_json` regression came from preserving all strings after adding stable sources: the existing numeric-string test expected `"0"` to become JSON integer `0`. Numeric strings are now normalized, while `/dev/v4l/by-id/...` paths and stream URLs remain strings. Atomic writes and ownership handling remain in place. This is separate from a path ending in `.json` being a directory.

`cyberwave edge microphones` and `cyberwave edge microphones --json` now list capture inputs. Linux reads ALSA capture PCMs from `/proc/asound`, including built-in inputs and USB devices, without requiring `arecord` or USB utilities. Playback-only cards are excluded. An ALSA capture PCM can be a line input rather than a physical microphone, and enumeration does not prove that recording permissions or audio routing are correct. The reported `hw:card,device` addresses are current-session addresses, not stable twin pins. Existing installation-time USB audio pinning remains responsible for assignments. macOS reuses AVFoundation enumeration and Windows reports its current limitation. There is no microphone `--save` mode because there is no equivalent shared configuration contract to write safely.

## The separate twin JSON directory errors

A directory at `~/.cyberwave/<uuid>.json` cannot be opened as a JSON file. One plausible cause is Docker's legacy bind-mount behavior creating a directory when a host source path was missing. The transcript does not prove which process created these directories. Inspect the exact paths and container mounts before changing them:

```bash
find "$HOME/.cyberwave" -maxdepth 1 -type d -name '*.json' -print
sudo systemctl stop cyberwave-edge-core.service
docker ps -a
# For each affected container:
docker inspect CONTAINER --format '{{json .Mounts}}'
```

Stopping Edge Core may leave driver containers running; stop affected containers before repairing their mounts. For each confirmed erroneous directory, inspect its contents and rename it to a unique backup path, preserving anything inside. Do not recursively delete the configuration directory. Then rerun `sudo cyberwave edge install` to fetch the twin files and rebuild the service configuration; verify the paths are regular JSON files and inspect service/driver logs. Use the configuration directory from the service and installation output, which may differ from root's home. Do not automatically replace directories inside a low-level JSON writer: they may contain data, and mount repair belongs at the deployment boundary.

## Follow-up: restart appears stuck on the testbed

The subsequent screenshots establish CLI 0.12.7 in both the operator's shell and
sudo, Edge Core package 0.1.18, and v4l2-ctl 1.26.1. APT reported those Cyberwave
packages as its newest candidates after a successful index refresh. The installed
CLI explicitly rejects `edge update` and `edge upgrade`; examples for newer CLI
commands therefore do not apply to this installation. `/usr/bin/cyberwave` and
`/bin/cyberwave` can be the same executable on a merged-/usr Ubuntu installation,
so those two paths alone are not evidence of conflicting installations.

`systemd-detect-virt` now returns `none`. The USB tree still contains no camera.
The kernel logs show recurring Wi-Fi rekey failures followed by
`4WAY_HANDSHAKE_TIMEOUT`, deauthentication and reconnection. That is evidence of
Wi-Fi instability, but not evidence that a particular Edge Core restart was held
up by it. `dmesg -w` prints historical buffer contents before following new events;
the screenshots cover a large span of uptime. The AppArmor denials identify CUPS
and the snap firmware notifier, not Edge Core. No supplied screenshot contains the
service journal or its state during the waiting restart.

The CLI prints exactly the command it invokes: `systemctl restart
cyberwave-edge-core.service`. Both observed waits therefore share the systemd job.
The repository's `restart_service` uses the blocking form, while installation uses
`restart --no-block`. The unit template uses `Type=notify`, and `main.py` sends
readiness only after `run_startup_checks` returns. Startup includes backend and
broker checks, twin synchronization and driver setup. The template sets a
900-second startup timeout; image-pull code can extend that timeout. Shutdown also
has work to do: the runtime stops the worker and then managed driver containers,
with individual driver-stop subprocesses bounded at 15 seconds in the current
source. These are candidate explanations from the checkout, not proof of the
exact code or unit settings deployed in package 0.1.18.

Inspect the existing job in a second terminal before issuing another restart:

```bash
sudo systemctl status cyberwave-edge-core.service --no-pager -l
sudo systemctl list-jobs --no-pager
sudo systemctl show cyberwave-edge-core.service --no-pager \
  -p Type -p ActiveState -p SubState -p Result -p MainPID -p ControlPID \
  -p TimeoutStartUSec -p TimeoutStopUSec -p NotifyAccess -p StatusText
sudo systemctl cat cyberwave-edge-core.service --no-pager
sudo journalctl -u cyberwave-edge-core.service -b -n 120 --no-pager
```

An `activating/start` service with an Edge Core PID suggests startup has not yet
reported readiness; inspect the last startup phase in the journal. A
`deactivating/stop-*` state indicates the old process or its stop work has not
finished. A waiting job with no new process can be held behind a dependency such
as Docker; `list-jobs` distinguishes that from application startup. `failed` needs
the journal's failure reason. If startup checks finish but the unit remains
activating, inspect readiness notification handling and the effective
`NotifyAccess` setting before changing the unit. Do not change `Type=notify` to
`simple` merely to make the shell return: that changes the readiness contract.

To correlate connectivity with this attempt, use recent logs rather than the full
kernel backlog:

```bash
sudo journalctl -k -b --since '-15 min' --no-pager
sudo journalctl -u NetworkManager -b --since '-15 min' --no-pager
sudo journalctl -u docker -b -n 80 --no-pager
sudo timeout 15s docker ps -a
```

If the Edge Core journal shows image pulls or network requests stalling alongside
current Wi-Fi disconnects, try a stable wired connection and repeat the operation.
A successful APT update proves connectivity at that time, not uninterrupted access
to the backend, broker or container registries. No network configuration change is
justified solely by the kernel screenshots.

When a new restart is actually needed, its nonblocking form works independently
of the CLI version:

```bash
sudo systemctl restart --no-block cyberwave-edge-core.service
sudo journalctl -u cyberwave-edge-core.service -n 80 -f
```

The first command confirms that a job was queued, not that the service became
healthy. The second intentionally follows logs until Ctrl-C. Interrupting a
waiting systemctl client does not reliably cancel the already queued systemd job;
inspect it instead of repeatedly restarting. The earlier `lines 1-1/1 (END)` screen
was a pager, not a hanging service; `q` exits it, and `--no-pager` avoids it. A bare
`>` shell prompt after `dpkg-query` indicates incomplete quoting; Ctrl-C cancels
that input. For a simpler package version check without format-string quoting,
use `dpkg-query -W cyberwave-cli cyberwave-edge-core`.

The implemented lifecycle limits below bound these waits and report job submission
accurately. The remote journal is still needed to identify the original blocked phase.

## Follow-up: no prompt for UMA-16 Left and Right

The reinstall screenshot's message, `Linux microphone twins bind-mount /dev/snd
into Docker`, identifies the older installation path. In the parent of commit
`fc046e666a` that branch only prints the mount explanation; it does not enumerate
Linux audio cards or ask which twin owns each device. The commit adds interactive
Linux audio pinning. Reinstalling Edge Core runs the installed CLI again and does
not independently upgrade that CLI. The public package and this source checkout
can therefore have different installation behavior. Verify the actual executable
with `sudo cyberwave --version` rather than assuming the current public package
contains an unreleased source change.

Mounting `/dev/snd` gives containers access to audio devices; it does not assign
Left and Right. UMA-16 units are USB audio interfaces, not USB serial adapters such
as `/dev/ttyUSB0`. The relevant stable names are `/dev/snd/by-id` and
`/dev/snd/by-path`. A unique hardware identity can be pinned by id; devices with a
shared udev identity must be pinned by physical USB port. A port pin follows the
port, so swapping identical units between ports swaps which physical unit serves
the twin. Left/right names themselves cannot identify hardware.

The newer CLI reads selected twins' audio sensor definitions and enumerates USB
ALSA cards. When two compatible unassigned arrays are present, it asks for the
first twin and can infer the only remaining device for the second. When saved
bindings exist, it verifies them and asks whether to retain them when several
cards are present. If no pinnable hardware exists, it reports the unassigned twins
instead of presenting an empty selection prompt. Camera discovery finding no
cameras does not bypass audio pinning.

Regression tests now cover two 16-sensor UMA twins with both unique and shared USB
serial identities, changed ALSA card numbers, missing hardware, a failed pin save,
and a complete mocked Linux setup flow with no cameras. The selection tests use
the real association logic and fake kernel/udev trees; package installation,
backend writes and service control are stubbed. They establish the CLI behavior,
not successful recording on the remote host or availability in a public release.

There is a remaining runtime limitation: skipping an assignment does not disable
the twin. The generic-microphone driver permits a default input when neither a
serial nor an audio device is configured, so two unpinned twins may open the same
input (or contend for it). Do not interpret an installer skip as a prohibition on
capture. The CLI now says this explicitly, reports missing credentials as a failed
pin save, and warns when selected audio assignments could not be persisted.
Existing explicit stable pins are handled more strictly by the driver, but that
does not guarantee uniqueness for unconfigured twins. No driver behavior is changed
by these diagnostic improvements.

Before relying on a Left/Right installation, inspect the actual devices and both
twins' saved metadata. The two `audio_device` paths must identify distinct cards,
and the driver logs must confirm the expected capture sources. These read-only
commands work without a new CLI:

```bash
sudo cyberwave --version
dpkg-query -W cyberwave-cli cyberwave-edge-core
cat /proc/asound/cards
ls -l /dev/snd/by-id/ /dev/snd/by-path/
for card in /dev/snd/controlC*; do
    [ -c "$card" ] || continue
    echo "$card"
    udevadm info --query=property --name="$card" | grep -E '^ID_(SERIAL|PATH)='
done
```

If the arrays still do not appear in the USB/ALSA inventory, connect and identify
them before expecting an assignment prompt. The earlier USB inventory contained
neither UMA array. The new screenshot alone does not establish that this changed.

## Implemented lifecycle limits

The lifecycle changes following this investigation replace the earlier proposed
CLI-only workaround. On Linux, `edge start`, `edge restart`, and the service-start
step of `edge install` submit jobs with `--no-block`. Systemctl submission has a
15-second timeout, and success means the request was accepted, not that the service
is ready. The CLI prints status/journal commands and installation no longer claims
that a queued startup is already running. A restart uses systemd's normal job
replacement behavior for that unit; it does not enumerate and cancel unrelated
jobs or depend on a job ID that changes with every attempt.

`edge stop` and `edge uninstall` queue a stop, replacing a pending start, and wait
up to 120 seconds for an inactive/failed service with no main PID, control PID or
pending job. Each state query has its own timeout. A timeout or query error returns
a failure with diagnostics. Uninstall does not proceed to disable/remove the unit,
stop containers, delete registrations or delete configuration until shutdown is
confirmed. A missing unit is accepted only when systemd reports it inactive with
no processes or job. Interrupting the CLI still does not cancel a submitted job;
inspect service state before retrying.

Driver-container stop and worker-container removal calls are bounded at 60 seconds.
Container inventory failures and cleanup errors abort uninstall before configuration
is removed. The exception is a Docker daemon that is not running: the listing fails
with Docker's "Cannot connect to the Docker daemon" error (or, in newer releases, "check
if the path is correct and if the daemon is running"), no container can be running,
so uninstall warns and continues. Containers that carry a restart policy start again
with the daemon and are then left to remove by hand. Package
installation/downloads and optional image pruning retain their own behavior; these
limits address service lifecycle jobs, not a promise that the entire installer
always finishes within 120 seconds.

Edge Core now stops sending timeout extensions immediately when SIGTERM arrives,
including during boot. If boot returns after shutdown was requested, it performs
cleanup without sending `READY=1`. Startup timeout extensions are capped at an
absolute 30-minute budget measured from watchdog creation, so repeated pull or
health heartbeats cannot perpetually extend a stuck startup. The base startup
timeout remains 900 seconds: without extensions a stalled boot fails sooner.
Normal runtime watchdog behavior remains unchanged.

The Edge Core-owned unit explicitly uses `TimeoutStopSec=90`,
`KillMode=control-group`, and `SendSIGKILL=yes`. This bounds graceful shutdown before
systemd escalates, including when startup is blocked in a library call. The unit
also sets `JobTimeoutSec=35min` so a job cannot wait forever behind a dependency.
That last setting bounds the job, not the lifetime of every process; a job timeout
alone is not a substitute for the service timeouts. Uninterruptible kernel I/O or
a nonresponsive system manager can still defeat ordinary process termination, in
which case the CLI returns a failure rather than waiting indefinitely.

Deploy both the CLI and Edge Core changes. Updating only the CLI makes commands
responsive but does not add the new watchdog behavior. Updating only Edge Core
does not change an old CLI's blocking restart call. The packaged Edge Core unit
refresh preserves the executable and configuration directory; `edge install` also
regenerates the unit. Existing drop-ins can override the defaults, and an already
running old process does not gain the new shutdown handler until it exits.

After deployment inspect the effective settings:

```bash
sudo systemctl cat cyberwave-edge-core.service --no-pager
sudo systemctl show cyberwave-edge-core.service --no-pager \
  -p TimeoutStartUSec -p TimeoutStopUSec -p JobTimeoutUSec -p KillMode -p SendSIGKILL
sudo cyberwave edge restart
sudo systemctl status cyberwave-edge-core.service --no-pager -l
sudo journalctl -u cyberwave-edge-core.service -n 100 --no-pager
```

Portable tests exercise submission errors, total stop deadlines, pending jobs,
configuration retention, Docker cleanup timeouts, capped watchdog extensions and
SIGTERM during boot. Native systemd integration and Debian postinst execution need
a Linux runner with systemd/GNU timeout; they are skipped on a Mac without those
tools. These tests do not establish that the remote host's original startup cause
has been repaired; that still requires its service journal.

### Run lifecycle regressions on Linux

The Linux CLI and Edge Core CI jobs include the lifecycle regressions. The staged
Debian integration job also runs them inside its isolated Ubuntu systemd container;
missing systemd fails that run instead of silently skipping integration coverage.
The live tests exercise readiness, repeated startup heartbeats, restart during boot,
and a process that remains alive after SIGTERM, using shortened test deadlines.

To run the lifecycle checks without building a Debian package, from the repository root:

```bash
LIFECYCLE_ONLY=1 bash cyberwave-edge-core/scripts/test_deb_upgrade_under_systemd.sh
```

This builds a disposable privileged Linux container with systemd as PID 1, installs
CLI and Edge Core from the checkout, runs both suites and removes the container.
It requires Docker and network access for Ubuntu and Python packages. On macOS,
Docker supplies the Linux VM; it does not exercise the host service manager.
