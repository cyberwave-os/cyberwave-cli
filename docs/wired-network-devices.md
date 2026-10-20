# Wired network devices: discovery and pinning

This document explains how `cyberwave edge install` finds hardware plugged into
the edge host's Ethernet ports (a FLIR AX8 thermal camera, an IP camera, a PLC,
a LiDAR) and pins each twin to its own device. It is written for engineers who
maintain the CLI, write asset metadata for a new kind of IP device, or debug an
install on a customer's machine. The USB side of pinning (cameras and audio
through udev links) is covered in the CLI README under `cyberwave edge install`.
This document picks up where that one stops: devices that have no `/dev` node
and whose address can change.

The code lives in `cyberwave_cli/discovery/` (the mechanics) and in
`cyberwave_cli/core.py` (the install step that drives them). The tests are
`tests/test_network_discovery.py` and `tests/test_network_binding.py`.

## Why this exists

A USB camera has a stable name the moment it is plugged in. An IP device does
not. Its address depends on the network it lands in. Behind a router it gets
whatever the DHCP server hands out. On a bare switch with no DHCP server, many
devices, the AX8 among them, fall back to a link-local address somewhere in
`169.254.0.0/16`, chosen at random. Some devices keep a factory-default static
address such as `192.168.1.201`.

Before this work the only way to point a driver at such a device was a driver
param, `-e CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://<ip>/avc`, edited by hand on
each twin. The FLIR AX8 asset still ships a placeholder,
`rtsp://192.168.X.X/avc`, which nothing can connect to. Worse, a twin with no
usable address was treated as an ordinary camera twin and offered the first USB
webcam, so the "thermal camera" streamed a C920.

The install now does what an engineer would do by hand. It looks at the wired
ports, finds the device, identifies it by its MAC address, works out which twin
it belongs to, makes sure the host can actually reach it, and writes the
binding onto the twin. It does all of this from metadata alone, so supporting a
new kind of device means writing a metadata block, not changing the CLI.

## At a glance

```
twin metadata (network_discovery on asset, twin block merged over it)
        │
driver param -e CYBERWAVE_METADATA_<FIELD>=…  ── usable → check it at its address
        │                                       └ placeholder → ignore, search instead
physical Ethernet ports ── no link → only usable params bind
        │               ├─ link, no IPv4 → offer DHCP + link-local (NetworkManager)
        │               └─ fixed IP on another subnet → offer an address next to it
DISCOVER per port, in parallel
   mDNS browse ─┐   ARP sweep (≤ /24) ─┐   fixed targets ─┐   neighbour MACs ─┐   TCP port probes
                └──────────────► one device record: ip, mac, vendor, hostname, services, open ports
MATCH   name_patterns filter, then score: mDNS 100 + MAC prefix 50 + open ports 25
ASSIGN  shared param → saved MAC → elimination → ask → param vs. scan → leftovers listed
BIND    fill bind templates ({ip} {mac} {hostname} {port} {instance} {interface} {txt_<key>})
        + mac_address, clear stale /dev pins, drop a superseded param,
        check TCP ports + RTSP DESCRIBE
SAVE    twin metadata (backend) + ~/.cyberwave/<twin>.json + cameras.json twin_to_device
        + the same entry in Edge.metadata.cameras / edge.json
```

Nothing in the code knows what an AX8 is. A new IP device needs only a `network_discovery`
block on its asset; `cyberwave scan --wired --json` shows the service types, names and TXT
records to put in it. The rest of this guide expands each line of this picture: the full flow
below, the metadata reference, the rules for several twins and sources, every prompt and its
default, the NetworkManager changes, and recipes for other kinds of device.

## The big picture

```
                                   cyberwave edge install
                                           │
          twin selection ──────────────────┤
                                           ▼
 ┌──────────────────────────── network binding (core._detect_and_bind_network_devices)
 │
 │  1. WHO NEEDS A DEVICE     every selected twin whose asset or twin metadata
 │                            has a `network_discovery` block → DiscoverySpec
 │                            (asset block = the hardware model,
 │                             twin block merged over it = this unit)
 │                                           │
 │  2. FIXED SOURCES          a driver param -e CYBERWAVE_METADATA_<FIELD>=value
 │                            for one of the spec's `bind` fields
 │                              usable (IP or host name) ─► kept, checked later
 │                              placeholder / malformed ─► ignored, device searched for
 │                                           │
 │  3. THE WIRE               physical Ethernet ports only (no Wi-Fi, docker, veth)
 │                              no carrier on any port ─► only usable fixed sources bind
 │                              link, no IPv4 ─► offer DHCP + link-local (NetworkManager)
 │                              fixed IP off-subnet ─► offer an address next to it
 │                                           │
 │  4. DISCOVER (discovery.hardware.discover, per addressed port, in parallel)
 │       mDNS / DNS-SD browse ─────┐
 │       ARP sweep (≤ /24) ────────┼─► NetworkDevice(ip, mac, vendor, hostname,
 │       fixed-source targets ─────┤                  services, open_ports, reachable)
 │       neighbour table (MACs) ───┤
 │       TCP port probes ──────────┘
 │                              announced from another subnet ─► offer an address, rescan
 │                                           │
 │  5. MATCH & ASSIGN         score = mDNS service 100 + MAC prefix 50 + open ports 25
 │                            (name_patterns filter first)
 │                              a. a fixed source shared by several twins → one owner
 │                              b. saved mac_address → the exact unit, wherever it is
 │                              c. elimination: bind what every assignment agrees on
 │                              d. ask when several devices fit
 │                              e. fixed sources checked at their address; ask when the
 │                                 scan offers other devices and the saved MAC does not
 │                                 settle it
 │                                           │
 │  6. BIND                   render the spec's `bind` templates with the device
 │                            ({ip} {mac} {hostname} {port} {instance} {interface}
 │                             {txt_<key>}), add mac_address, clear stale /dev pins,
 │                            drop a driver param that would override the binding,
 │                            probe tcp_ports and the RTSP path
 │                                           │
 │  7. PERSIST                twin metadata (backend) + ~/.cyberwave/<twin>.json,
 │                            stream URLs into cameras.json twin_to_device
 │                            and Edge.metadata.cameras / edge.json
 └───────────────────────────────────────────┘
                                           ▼
                        USB camera pinning ─► USB audio pinning ─► service install
```

Network binding runs before USB camera pinning on purpose. A twin served over
Ethernet is never offered a USB camera, and when every camera twin is a network
twin, the USB camera step is skipped. `cyberwave edge install
--reconfigure-camera` runs the same two steps in the same order, so an AX8 can
be re-pinned without a full reinstall.

## Opting in: the `network_discovery` block

A twin takes part when its asset's metadata, its own metadata, or both carry a
`network_discovery` block. The asset's block describes the hardware model: how
to recognise it and what to write once it is found. The twin's block is merged
over it for per-unit settings, so the asset says "this is how you find an AX8"
and the twin can add "and this one lives on `10.20.30.0/24`". The merge is
shallow, except `bind`, which merges per field.

This is the block the FLIR AX8 asset uses today:

```json
"network_discovery": {
  "methods": ["mdns"],
  "mdns_services": ["_flir-ircam._tcp"],
  "mac_prefixes": ["00:40:7F", "00:1B:D8", "00:13:56"],
  "link_local": true,
  "subnet_prefix": 24,
  "tcp_ports": [554],
  "timeout": 3,
  "bind": { "video_device": "rtsp://{ip}/avc" }
}
```

| Key | Meaning |
| --- | --- |
| `bind` | **Required.** The twin metadata to write once the device is found, as templates. See [Templates](#templates). |
| `methods` | How the device is recognised: any of `"mdns"`, `"arp"`, `"ports"`. Defaults to `["mdns"]` when `mdns_services` is set. The ARP sweep and the port probes run only for twins that list them. |
| `mdns_services` | DNS-SD service types the device announces, such as `_flir-ircam._tcp`. A trailing `.local` is accepted. |
| `mac_prefixes` | MAC prefixes (OUIs) of the device's network interface. Used by `"arp"`, and also adds to the score of a device found any other way. |
| `name_patterns` | Shell-style patterns, case-insensitive (`"FLIR AX8*"`). When set, a device fits only if one pattern matches its mDNS instance name or host name. Use it to narrow a service type that many devices share, such as `_rtsp._tcp` or `_http._tcp`. |
| `tcp_ports` | Ports that should accept a connection. Always checked after binding, where a closed port is reported. Under `"ports"` they are also the signature that identifies the device. A single number is read as a one-item list. |
| `subnet_prefix` | Prefix length of the device's subnet, default 24. Used when the host needs an address next to the device. |
| `host_address` | The address (CIDR) the host takes on the device's subnet, instead of one picked automatically. Ignored, with a warning, when it is not on the device's subnet. |
| `link_local` | The device falls back to link-local addressing without DHCP. A wired port with no address is then offered DHCP plus link-local. |
| `timeout` | Seconds spent discovering, default 2.5, at most 60. With several twins the longest timeout wins. |

A block that cannot bind anything (no `bind`) or cannot locate anything (no
usable method and no fixed source) is ignored, and that twin is treated as an
ordinary twin. Unknown methods, malformed MAC prefixes and out-of-range numbers
are dropped silently rather than failing the install.

### Templates

`bind` maps metadata keys to Python format strings. When the device is found
they are filled in with these fields:

| Field | Value |
| --- | --- |
| `{ip}` | The device's IPv4 address. When it answers on both a DHCP and a link-local address, the one that is not link-local. |
| `{mac}` | Its MAC address, `aa:bb:cc:dd:ee:ff`. |
| `{hostname}` | Its mDNS host name without `.local`. |
| `{port}` | The port of the matched mDNS service, else the first open `tcp_ports` entry, else empty. |
| `{instance}` | The matched mDNS service's instance name. |
| `{interface}` | The host port it was found on, such as `eth0`. |
| `{txt_<key>}` | A TXT record of the matched mDNS service, key in lower case (`{txt_path}`, `{txt_sn}`). Empty when the record is absent. |

A template that names any other field is dropped when the spec is read, so a
typo shows up as "the twin does not bind that key" instead of a crash halfway
through an install.

## Where the address can come from

A twin's device can be named in two ways, and the rules for combining them are
the heart of this feature.

**A fixed source** is a driver param that sets one of the spec's `bind` fields
directly: `-e CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://10.20.30.3/avc`. It is read
from the twin's own drivers, or from the asset's drivers when the twin has none.
That is the same rule edge-core uses when it launches the driver. Edge-core
hands such a param to the container ahead of the twin's metadata. For cameras it
also outranks the `cameras.json` mapping. So a fixed source always wins at
runtime, and the install never writes the same field into the metadata: it would
be dead data.

**The wire** is everything the scan finds on the Ethernet ports.

`hardware.source_problem` decides whether a fixed source is usable. An IPv4
address or a valid host name is usable. Anything else is not, and the reason is
spelled out: `192.168.X.X is a placeholder, not an address`, `<ip> is a
placeholder…`, `192.168.1 is not a valid IP address`, `it names no host`, `it is
not a valid URL`, `it is empty`.

The three base rules:

1. **A usable fixed source is followed.** With a link, the device at that address
   is checked (see below). Without a link there is nothing to check, so it is
   used as set, and the summary says `not checked`.
2. **An unusable fixed source is ignored while a wired port has a link.** The
   install prints why (`ignoring CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://192.168.X.X/avc
   (192.168.X.X is a placeholder, not an address); searching the wired network
   instead`) and searches for the device. Once the device is bound, the
   placeholder is removed from the twin's driver params, because edge-core would
   otherwise hand it to the driver ahead of the new binding. When the param came
   from the asset's drivers, the twin gets its own copy of those drivers without
   it. A placeholder whose device is not found is left alone: it is harmless.
3. **No link and no usable fixed source means no binding**, and the summary says
   so: `no wired port has a link and no CYBERWAVE_METADATA_VIDEO_DEVICE is set`.

## Several twins, several devices, several sources

One host often serves several IP devices, and one asset often has several twins.
The install handles every combination with the same few steps, in this order:

**a. One fixed source, one twin.** Twins without drivers of their own inherit the
asset's, so a fixed source set on the asset names the same device for every
twin of that asset. That is the classic case of "more twins than addresses".
The install groups twins by identical fixed value and settles each group:

- the twin whose saved `mac_address` answers at that address keeps it, with no
  question asked;
- otherwise the operator is asked `Which twin is it for` (`s` leaves it on all of
  them, with a warning that they will all show the same device);
- every other twin in the group loses the param from its drivers and searches the
  wire for its own device. If it cannot search (no link, or no method), it is
  left unpinned with the reason `its CYBERWAVE_METADATA_VIDEO_DEVICE names Cam
  left's device…`.

Twins whose sources share an IP but differ otherwise (`rtsp://nvr/ch1` and
`rtsp://nvr/ch2` on one recorder) are not a conflict and are left alone.

**b. Saved MACs first.** A twin that was bound before carries `mac_address`. If a
device with that MAC answers anywhere on the wire, the twin gets it back: `Cam
right: using its saved device …`. This is what keeps two identical units from
swapping when their addresses change. A device whose MAC is saved on a twin that
uses a fixed source is reserved for that twin, even if it moved to another
address, so another twin can never take it.

**c. Elimination.** The twins left without a device are matched by
`_infer_associations`, the same routine the USB flow uses. A twin is bound
without a question only when every complete assignment of twins to devices
agrees on its device. The summary says why: `the only attached device it can
use`, or `the only device left once the other twins' verified bindings and
sensor types are accounted for`.

**d. Asking.** When the evidence does not settle a twin, the operator picks from
the devices that fit, best score first, labelled with vendor, MAC, IP, port and
mDNS instance name (`Check the MAC address on the device's label`). Each answer
can settle the rest. Two identical units need one question, not two. When more
twins than devices compete, the prompt says so (`2 twins (Cam left, Cam right)
fit it: a twin left without one stays unpinned`), and the twin left over is
reported as `every matching device found is pinned to another twin (Cam left)`
rather than "not found".

**e. Fixed sources against the scan.** Last, each twin still on a usable fixed
IP is checked at that address. If its saved MAC answers there, the source is
confirmed and nothing is asked. Otherwise, if the scan found other devices that
fit the twin and that no other twin took, there is more address information than
the twin needs, and the operator chooses:

```
  AX8: CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://10.20.30.3/avc (nothing answers there), and the wired scan found:
    1) keep CYBERWAVE_METADATA_VIDEO_DEVICE=rtsp://10.20.30.3/avc
    2) Flir Systems 00:40:7f:14:8b:73 169.254.150.216 (eth0) · FLIR AX8 - 71234297 (the MAC saved on this twin)
  Source for AX8 [1/2] (1):
```

Keeping the param is the default, and it is also what happens with no terminal.
Choosing a device writes the binding into the metadata and removes the param
from the twin's drivers, so the choice actually reaches the driver. When nothing
else fits, the source is followed as before, with a warning when nothing answers
there or a different MAC does.

Matching devices that no twin took are listed at the end (`Not pinned to any
twin: …`), so a spare camera on the switch is visible rather than silently
ignored.

The combinations at a glance:

| Situation | Outcome |
| --- | --- |
| 1 twin, 1 matching device | Bound automatically. |
| 1 twin, 2+ matching devices | Asked, unless a saved MAC picks one. |
| 2 identical twins, 2 devices, nothing saved | One question; the other twin is inferred. |
| 2 twins, 1 device | Asked which twin gets it; the other twin is unpinned, and the reason names the holder. |
| 2 twins inheriting 1 asset fixed source | Saved MAC decides, else asked; the others search for their own device. |
| Fixed source confirmed by its saved MAC | Kept, no question; spare devices are listed. |
| Fixed source not confirmed, other fitting devices free | Asked: keep the param or take a device. |
| Fixed source, no other fitting device | Kept; warns if nothing or a different MAC answers. |
| Placeholder fixed source, link up | Ignored, device searched; the param is removed once bound. |
| Usable fixed source, no link | Used as set, `not checked`. |
| Placeholder or no fixed source, no link | Not pinned; the reason says so. |
| Device announces from another subnet | Asked to add an address next to it, then rescanned. |

### The questions and their defaults

| Prompt | When | Default | No terminal |
| --- | --- | --- | --- |
| `Which twin is it for` | A fixed source set on several twins, no saved MAC answering there | `s`: leave it on all | Leave it on all |
| `Device for <twin>` | Several devices fit, or several twins want one device | `s`: leave unpinned | Unpinned |
| `Source for <twin>` | A fixed source not confirmed, other fitting devices free | `1`: keep the param | Keep the param |
| `… Which port is it on?` | A fixed IP and several linked ports | First port | Skipped |
| `Add <address> to <port>?` | A fixed IP or an announced device outside every port's subnet | Yes | No |
| `Set it to take an address from DHCP … and a link-local one …?` | A linked port with no IPv4 and a twin whose spec sets `link_local` | Yes | No |
| `Add link-local addressing to <port>?` | A link-local device seen on a port that only has DHCP or static addresses | Yes | No |

Every default leans towards changing nothing. An unattended install never
rewrites a driver param and never touches NetworkManager.

## The wired port and NetworkManager

A device can only be found on a port that has an IPv4 address. Ports are
configured through NetworkManager (`cyberwave_cli/discovery/linkconfig.py`)
rather than with `ip addr add`, so the configuration survives a reboot. Nothing
is changed without the operator's yes. When NetworkManager is not running, the
install prints the addresses to set by hand.

Device networks come in two shapes, and the port is set up for both at once:

- **A bare switch with no DHCP server.** This is the AX8 on the bench today.
  Devices fall back to link-local addresses, so the host needs one too.
- **A router running DHCP.** Devices get addresses from it, and so should the
  host.

The profile therefore runs DHCP and link-local side by side, plus any static
address a device's subnet needs. Plug the same host into a router tomorrow and
it takes a lease without anyone re-running install. Keep it on the switch and
link-local keeps working.

| Situation on the port | What install does |
| --- | --- |
| Idle (no profile running) | Creates `cyberwave-<port>`: `ipv4.method auto` (DHCP), `ipv4.link-local enabled`, `ipv4.dhcp-timeout 2147483647` (keep asking forever, so a router plugged in later is used), `ipv4.never-default yes`, `ipv6.method link-local`, autoconnect, plus any static addresses with a DAD timeout. |
| Another profile running (the uplink's DHCP, a static lab profile) | Adds to that profile in place: `+ipv4.addresses` and/or `ipv4.link-local enabled`, then `nmcli device reapply`. Its method, lease and default route stay. Activating a second profile would have replaced it and could have cut the host off. A running profile with IPv4 `disabled` is reported, not changed. |
| `cyberwave-<port>` already exists | Updated to the shape above and brought up again. |
| `cyberwave-<port>` from an earlier install, link-local only | Upgraded at the start of the network step with no question, since the install made it: `<port>: cyberwave-<port> now also takes a DHCP address when a router serves one`. |
| A profile created here fails to come up | Deleted again, so it cannot take the port at the next boot. |

`ipv4.link-local` beside other addressing needs NetworkManager 1.40. On older
versions an idle port falls back to a link-local-only profile, or to DHCP plus
the static addresses. An old link-local-only profile is then left as it is, and
link-local cannot be added beside someone else's profile (the error says so).
Debian bookworm and Raspberry Pi OS ship 1.42 or later; the bench Pi runs 1.52.

The port never takes the default route (`ipv4.never-default yes`), so a camera
network's router can never steal the uplink or its DNS. The other side of that
choice: a host whose only uplink is that Ethernet port gets an address from the
router but no default route through it. Make that port's uplink profile the
running one before install, and install will add to it instead of creating its
own.

Two more address cases are handled during discovery, not before it:

- **A fixed IP outside every port's subnet.** The host takes `host_address` when
  the spec sets one on the device's subnet. Otherwise it takes the highest free
  address of the subnet, below the few at the top that gateways and switches
  tend to hold. Twins sharing one subnet ask once.
- **A device announcing an address the host cannot reach.** mDNS answers arrive by
  multicast even from another subnet, so the device is seen but marked
  unreachable. The install offers an address on its subnet and scans again. A
  link-local device seen while the port only has DHCP gets the port's
  link-local addressing switched on instead of a fixed `169.254` address, which
  would skip conflict detection.

To check the result on a host:

```bash
nmcli -g GENERAL.CONNECTION device show eth0
nmcli -g ipv4.method,ipv4.link-local,ipv4.dhcp-timeout,ipv4.never-default connection show cyberwave-eth0
#   auto / enabled (some versions print 3) / 2147483647 / yes
ip -4 addr show eth0          # a 169.254.x.y/16 address, plus a DHCP one when a router is there
```

Uninstall does not remove `cyberwave-*` profiles yet. Remove one by hand with
`sudo nmcli connection delete cyberwave-eth0` when retiring a host.

## How discovery works

`hardware.discover(ports, …)` runs on every port that has a carrier and an IPv4
address, one thread per port and method, and merges what it learns into one
`NetworkDevice` per `(port, ip)`.

**mDNS / DNS-SD** (`discovery/mdns.py`) is a small standard-library browser. It
needs neither avahi nor root. It sends PTR queries for the wanted service types
(or for every type, under `cyberwave scan --wired`), then follows up with SRV, TXT
and A queries for what is missing. It listens on two sockets. An ephemeral port
receives legacy unicast replies (RFC 6762 §6.7) and coexists with a running
avahi-daemon. A socket on 5353 receives multicast replies from devices that
cannot route back to the host, which is exactly the "device on another subnet"
case. Malformed packets are skipped, never fatal.

**The ARP sweep** (`discovery/neighbors.py`) needs no raw sockets either. It sends
an empty UDP datagram to the discard port of every host on the port's subnet.
The kernel has to ARP for each one, and every answer lands in `/proc/net/arp`,
which is read back after a short wait. Only subnets up to a /24 are swept. A
link-local /16 has 65,534 hosts, and poking them all would overflow the kernel's
neighbour table (`gc_thresh3` is 1024 by default) and could evict the gateway's
entry. On a /16 the device has to be found by mDNS, or named by a fixed source.

**MACs** for devices found by mDNS or named by a fixed source are resolved the
same way, by poking just those addresses. A MAC is only known for a reachable
device. The vendor is looked up in whichever OUI database the host has (nmap,
ieee-data, hwdata, misc).

**Port probes** run last, only for twins whose spec lists the `"ports"` method.
Each device that answered ARP is tried on those ports with a one-second connect,
in parallel, and the open ones are recorded as `open_ports`.

The host's own addresses are dropped, because the host answers mDNS on the same
link and is never a device to bind.

**Matching** (`hardware.match_score`) scores each device against each twin's
spec. `name_patterns`, when set, is a filter: no match, score zero. Then an
announced `mdns_services` type adds 100, a MAC with one of the `mac_prefixes`
adds 50, and all `tcp_ports` open under the `"ports"` method adds 25. Any score
above zero means the device fits, and higher scores are offered first.

**Verification** (`hardware.verify_binding`) runs after binding. It connects to
every `tcp_ports` entry and sends an RTSP `DESCRIBE` to any `rtsp://` value. A
200, 401 or 403 means the stream path exists; a 404 means the template's path is
wrong. Problems are printed (`binding it anyway`) but never block the binding,
since a camera that is still booting would otherwise lose its pin. `rtsps://` is
not probed, because that would need TLS. The probe never puts a URL's
credentials on the wire, and every URL the install prints has its credentials
redacted.

## What gets written

For each bound twin the install writes, best-effort, through the backend:

- the rendered `bind` values (unless a fixed source sets that field);
- `mac_address`, the identity used to recognise the unit next time;
- an empty value for any `bind` key that held a `/dev/...` path, which is a stale
  USB pin from before the twin was a network twin;
- `drivers`, only when a driver param had to go (a placeholder, a source that
  belongs to another twin, or a param the operator replaced with a discovered
  device).

The same keys go into `~/.cyberwave/<twin-uuid>.json`. Edge-core syncs that file
back to the backend, so leaving it stale would push the old binding back. When
the backend refuses the update, the binding still applies on this host and the
summary says `Used on this host (cameras.json), but the twin still holds its old
binding. Re-run: cyberwave edge install`.

Camera twins also get their stream URL recorded in `cameras.json` under
`twin_to_device`. Edge-core gives a camera twin that is missing from that map
the global `selected_device`, which is a USB camera, so leaving a network camera
out would hand it a webcam. An unbound network camera has any stale entry
removed. A placeholder never reaches that file.

Edge-core reads the backend copy of the mapping (`Edge.metadata.cameras`, cached
in `~/.cyberwave/edge.json`) before `cameras.json`, so the install also writes
the network twins' entries there, last, after the USB cameras: it uploads the
updated mapping, or, when the backend cannot be reached, writes it into
`edge.json` and says so. Only the network twins' entries change. Edge-core also
fills a twin missing from `edge.json` from `cameras.json`, so an install that
reached only `cameras.json` still hands the driver its URL.

The install ends the step with a summary:

```
Network devices
  AX8 → video_device=rtsp://169.254.150.216/avc (MAC 00:40:7f:14:8b:73 on eth0)
    Removed the placeholder param from AX8's driver params, so this pin is what the driver uses.
  Lab cam → video_device=rtsp://10.20.30.3/stream (MAC 00:11:22:00:00:03 on eth0; set by CYBERWAVE_METADATA_VIDEO_DEVICE)
  Spare twin → not pinned: every matching device found is pinned to another twin (AX8)
  Not pinned to any twin: Flir Systems 00:40:7f:14:8b:99 169.254.12.7 (eth0) · FLIR AX8 - 71234300
```

## Supporting another kind of IP device

Nothing in the discovery code knows what an AX8 is. Supporting a new device is a
matter of writing its `network_discovery` block on the asset, and making sure its
driver reads the keys the block binds.

**How a driver gets the binding.** Edge-core mounts the twin's JSON into the
container and sets `CYBERWAVE_TWIN_JSON_FILE`. A driver reads its keys from
`metadata` there. A driver param `-e CYBERWAVE_METADATA_<KEY>=...` sets the same
key and wins. A bound key `lidar_host` is therefore read by the driver from
`metadata.lidar_host`, or from `CYBERWAVE_METADATA_LIDAR_HOST` when its entrypoint
exports metadata that way, as the camera and microphone drivers do. A fixed
source works for any bound key, by the same naming.

**Start by looking.** `cyberwave scan --wired` lists every device on the Ethernet
ports, with MAC, vendor and every mDNS service it announces. `--json` adds
instance names, ports and TXT records, which is what you need to write
`mdns_services`, `name_patterns` and `{txt_*}` templates. On the bench AX8 it
shows `_flir-ircam._tcp` on port 22136 with TXT `SN=71234297`, so `{txt_sn}` is
the camera's serial number.

Some patterns, from the most specific signal to the least:

**A device that announces a service of its own** (FLIR, Axis, many robots): match
on the service type. This is the strongest signal, it crosses subnets, and it
works on a link-local /16.

```json
{ "mdns_services": ["_flir-ircam._tcp"], "link_local": true,
  "bind": { "video_device": "rtsp://{ip}/avc" } }
```

**A device that announces a generic service** (`_rtsp._tcp`, `_http._tcp`): add
`name_patterns`, or every camera on the switch will fit.

```json
{ "mdns_services": ["_rtsp._tcp"], "name_patterns": ["AXIS P13*"],
  "bind": { "video_device": "rtsp://{ip}:{port}/axis-media/media.amp" } }
```

**A device with a path in its TXT records**: use `{txt_<key>}`.

```json
{ "mdns_services": ["_http._tcp"], "name_patterns": ["acme-gateway-*"],
  "bind": { "api_url": "http://{ip}:{port}{txt_path}" } }
```

**A device that announces nothing but has a known vendor prefix**: sweep. This
needs a subnet of /24 or smaller.

```json
{ "methods": ["arp"], "mac_prefixes": ["60:76:88"], "subnet_prefix": 24,
  "bind": { "lidar_host": "{ip}" } }
```

**A device that announces nothing and has an anonymous MAC** (a Modbus PLC, an
embedded board): identify it by the ports it serves. `{port}` becomes the first
open one.

```json
{ "methods": ["ports"], "tcp_ports": [502],
  "bind": { "modbus_host": "{ip}", "modbus_port": "{port}" } }
```

Ports are the weakest signal (a PC with the same port open fits too), so
combine them with `mac_prefixes` when you can. With two methods each signal
adds to the score, and the better fit is offered first.

**A device at a factory-default static address** (many LiDARs ship on
`192.168.1.201`): give the twin a fixed source for its bind field and the
address the host should take next to it.

```json
"drivers": { "default": { "params": ["-e", "CYBERWAVE_METADATA_LIDAR_HOST=192.168.1.201"] } },
"network_discovery": { "methods": ["arp"], "mac_prefixes": ["60:76:88"],
  "host_address": "192.168.1.77/24", "bind": { "lidar_host": "{ip}" } }
```

The install adds `192.168.1.77/24` to the port, after asking, and records the
MAC answering at `.201`. If someone later re-addresses the LiDAR, the scan
finds it by its prefix, and the operator is offered the new address.

**Identical units** need nothing special. The first install asks once, and then
`mac_address` keeps each unit on its own twin across address changes.

## Using the discovery code elsewhere

The modules are plain Python with no CLI dependencies beyond `rich` for
printing, and can be reused directly:

```python
from cyberwave_cli.discovery import hardware, interfaces

spec = hardware.DiscoverySpec.from_metadata(asset_block, twin_block, explicit=driver_values)
devices = hardware.discover(
    interfaces.wired_interfaces(),
    mdns_services=spec.mdns_services,
    arp_sweep=spec.sweeps,
    probe_ports=spec.tcp_ports if spec.uses_ports else (),
    timeout=spec.timeout,
)
best = max(devices, key=lambda d: hardware.match_score(spec, d), default=None)
binding = hardware.render_bind(spec, best) if best and hardware.match_score(spec, best) else {}
```

`explicit_metadata_values(drivers)` turns a drivers block into the `{field:
value}` map of fixed sources, parsing `-e`, `--env` and `--env=` exactly as
edge-core does. `source_problem(value)` says whether a value can reach a device.
`linkconfig` can be used on its own to give a port an address the persistent
way.

## Testing

`tests/test_network_discovery.py` covers the mechanics with fakes: mDNS packet
parsing and the two-socket browse against a local responder, the ARP table
parser and sweep sizing, MAC and OUI helpers, the RTSP probe (path check,
credentials never sent, `rtsps` skipped), spec parsing and merging, scoring,
`name_patterns`, the `"ports"` method, templates including TXT fields, and every
NetworkManager command line (idle port, running profile, IPv4 disabled, old
profile upgrade, NetworkManager before 1.40, rollback of a failed profile).

`tests/test_network_binding.py` drives the whole install step with discovery
faked: fixed sources, placeholders, no link, host addresses, subnet offers, and
the multi-twin cases (a shared asset source, saved MACs, a moved unit, more
twins than devices, a source questioned against the scan, a device on two
addresses, a link-local device beside DHCP).

Run them with:

```bash
cd cyberwave-clis/cyberwave-python-cli
.venv/bin/python -m pytest tests/test_network_discovery.py tests/test_network_binding.py -q
```

On a real host, `cyberwave scan --wired` shows what discovery sees without
changing anything, and `sudo cyberwave edge install --reconfigure-camera` re-runs
the binding.

## Known limits

- IPv4 only. A device reachable only over IPv6 is not found.
- Wired ports only. Wi-Fi interfaces are not scanned: discovery is meant for the
  device network behind the host, not the office LAN.
- The ARP sweep and port probes stop at a /24. On a link-local /16, a device has
  to announce itself over mDNS.
- A fixed source naming a host name is used as set. It is not resolved or
  matched against the scan.
- ONVIF WS-Discovery and SSDP are not used for binding. The AX8 does not answer
  either, and `cyberwave scan` (without `--wired`) already offers them for
  browsing the LAN.
- mDNS answers are trusted as announced. A device on the same wire could
  announce a matching service. The saved MAC limits the damage to the first
  install.
- Uninstall leaves `cyberwave-*` NetworkManager profiles in place.
