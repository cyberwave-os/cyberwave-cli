# Network devices: preserve configuration, diagnose, then verify explicitly

Installation and shared pairing preserve the operator's host-network configuration.
They do not add addresses, alter routes or metrics, change DHCP/link-local settings,
activate profiles, or upgrade/delete existing `cyberwave-*` profiles. Existing profiles
are left as found, including profiles created by an earlier CLI version.

This intentionally replaces automatic host provisioning and unknown-device discovery.
The CLI uses local address/route inspection and cached neighbor records. It does not
send ARP sweeps, UDP pokes, mDNS/ONVIF/SSDP queries, ping, port sweeps or automatic RTSP
requests. No scanner binary or homemade scanning fallback is invoked. Missing optional
local OUI databases simply leave vendor labels unavailable.

These guarantees apply to CLI device diagnosis. Installation still performs its normal
cloud/API/download work; launched drivers and operating-system behavior are separate.

## What the observations establish

Every matching interface prefix is listed separately from the kernel-selected route,
including Wi-Fi, VPN and virtual interfaces. A route is the kernel's choice for the
CLI context, not proof that the intended device is on that path. Source-bound sockets,
policy routing and driver namespaces may differ. Inspection failures are reported as
unavailable, not as proof of missing addresses. Reads are bounded to five seconds.

The generic separate-cable advice compares other addressed physical interfaces.
Loopback and software-only interfaces do not trigger that advice. Actual matching
prefixes, selected routes and overlapping assigned prefixes still include virtual
interfaces, since a bridge or VPN can affect the path to a known endpoint.

Cached neighbors are keyed by interface and IP. They can be stale, duplicated or refer
to a gateway/proxy. Neither MAC/OUI nor a cached service record authenticates a device.
No new binding or interface association is inferred from them.

| Arrangement | Behavior |
| --- | --- |
| One interface, one subnet | Preserve configured endpoints and routes. |
| One interface, several prefixes | Consider all prefixes; do not assume one subnet. |
| Different interfaces, non-overlapping subnets | Report each path independently. |
| Different interfaces, overlapping prefixes | Report possible overlap; the interfaces may intentionally share one LAN. |
| Separate networks, same prefix or duplicate IP | Report ambiguity; no automatic route/interface winner or `/32` override. |
| Linked port without host IPv4 | Report missing host addressing; device addressing remains unknown. |
| Device not observed | Do not conclude that it lacks an IP or is powered off. |

For example:

```text
Go2 [linux-aarch64-jetson] ROBOT_IP: 192.168.123.161 (address reference)
Matching host prefix: wlan0 192.168.123.20/24
Kernel-selected route: wlan0, source 192.168.123.20 (CLI context)
eth0: cable connected, no IPv4 address; device addressing unknown
Intended device: not verified
```

If this robot is on the separate eth0 cable, its addressing overlaps the Wi-Fi network.
The CLI explains this condition even though eth0 has no prefix yet. It does not assume
which cable contains the robot. A working Wi-Fi endpoint is preserved. The simplest
operator remedy for separate overlapping networks is to renumber one network, including
its router/DHCP pool and devices where applicable. If that is impossible, the deployment
owner must provide explicit network isolation and application access.

## Profiles and endpoints

A private IP in `ROBOT_IP`, `MQTT_HOST` or another driver variable is an **address reference**,
not a requirement for a local Ethernet connection or a subnet mask. References retain
profile and variable attribution. Identical IPs share a route lookup, not a device identity.

Twin drivers override asset drivers. Inferred references and declared fixed fields use
one profile-selection snapshot. The CLI optionally imports the runtime resolver; it does
not implement another platform/child/default selection policy or install Edge Core to
obtain it. This standalone CLI does not have an Edge Core package dependency.

The CLI uses the existing Debian-package import path when Edge Core is installed outside
its Python environment. Successful pairing records a context fingerprint in
`environment.json` only after detach, attach and twin-cache synchronization succeed.
Selected cached twins must all be present, match that fingerprint, and identify their
attachments and attached assets. Their child registry IDs and the current host feed the
runtime selector. This describes the completed pairing snapshot, not proof of running drivers.

Legacy or incomplete pairing context leaves multi-profile selection unresolved; rerun
`cyberwave edge install` to refresh it. Missing/incompatible runtime resolution, malformed
configuration and unsupported service/environment overrides also remain unresolved.
A single profile does not need child context because child precedence cannot change it.

Unresolved profiles produce one status message. Inactive candidate addresses are not
queried, adopted or persisted. Existing saved endpoints, driver arguments, MAC pins and
local/cloud camera mappings remain intact. A network declaration stays classified as
network hardware, so profile uncertainty does not trigger USB reassignment. No matching
profile is distinct from a declaration that has no driver profiles at all.

A minimal metadata-only declaration can name the supported binding field:

```json
{"network_discovery": {"bind": {"video_device": "rtsp://{ip}/stream"}}}
```

Asset and twin declarations still merge. The interactive `bind` choice selects a twin
and declared field, accepts a known IP/hostname/URL from the operator, and asks before
saving it. It does not probe or infer a MAC, port or interface. A successful verification
also never persists a binding. Fixed driver endpoints are preserved; edit their source
configuration explicitly when replacing them. Existing declarations requesting `arp`,
`ports` or `mdns` are recognized but never executed. Historical `host_address`,
`subnet_prefix` and `link_local` fields no longer provision the host.

## Installation steps

1. Run `cyberwave edge install` or shared pairing. Review host observations and profile
   status. Noninteractive execution continues without device probes or network changes.
2. Preserve known endpoints. For an unknown endpoint, consult device documentation,
   inventory, its configuration interface or an existing DHCP server's lease view.
   Automatic unknown-device discovery is no longer available.
3. If the host lacks suitable addressing on a known physical link, ask the builder,
   customer IT team or deployment engineer to configure it outside the CLI. Confirm
   the device mask and unused host address; check all other interfaces for overlap first.
4. A device confirmed to require DHCP needs an explicitly provided DHCP service on that
   network, or a supported static-device configuration. Enabling a DHCP client on the
   edge does not provide DHCP to other devices. A bare-switch device may instead have a
   factory static address, link-local fallback or IPv6; absence of observations does not
   identify which case applies.
5. For a metadata-only or resolved declaration, use `bind` to supply a known endpoint.
   Use `refresh` for one fresh local/profile snapshot, or continue and rerun after external
   setup. EOF continues safely. A refresh does not repeat an active check.
6. Optionally select `verify`, supply one numeric IPv4 and documented TCP service port,
   review its route, then explicitly confirm the check. Generic Yes-to-all is insufficient.
   TLS options are available through the command below.

`--yes` skips this interactive network menu, including during camera reconfiguration.
It still reports local observations; it never accepts verification on the operator's behalf.

In a mixed deployment, a router-addressed Wi-Fi device and a router-addressed Ethernet
device retain their endpoints. An isolated unaddressed Ethernet link remains unresolved
until the operator configures the host and, if necessary, the device/DHCP service.

## One-endpoint verification

```bash
cyberwave scan --target 192.168.50.10
cyberwave scan --target 192.168.50.10 --port 554 --verify
cyberwave scan --target 192.168.50.10 --port 443 --verify --interface eth1 \
  --tls --server-name device.example --ca-file /path/to/trusted-ca.pem \
  --certificate-sha256 INDEPENDENTLY_PROVISIONED_CERTIFICATE_SHA256
```

Without `--verify`, inspection is local only. Explicit checks permit one application
connection, no retries, DNS target resolution, alternate ports, protocol guessing or
scan fallback. Connect and optional TLS share a five-second deadline and the same socket.
Public and loopback unicast IPv4 targets are supported when explicitly supplied; the
check is not limited to private device networks. Multicast, unspecified, reserved and
known subnet broadcast targets are rejected.
Necessary OS ARP and TCP retransmissions are ordinary transport behavior; one connection
is not a promise of one packet.

An interface constraint must be enforced by supported OS socket facilities before connect.
If unsupported or denied, the check is skipped without privilege escalation or fallback.
Ambiguous interface observations require an explicit interface constraint. No temporary
host address or route is created. The actual socket source is shown separately from the
route snapshot; neither proves the intended physical cable without further evidence.

| Result | Meaning |
| --- | --- |
| Route observed | Configuration only; intended device unverified. |
| TCP connection accepted | Selected service responded at this time; identity unverified. |
| TLS validation succeeded | Expected server name authenticated under configured trust. |
| Certificate SHA-256 matched | Independently supplied credential matched; shared certificates are not unique device identities. |
| Timeout/refusal/TLS failure/mismatch | Exact check failed; no insecure retry or binding replacement. |

TLS requires a trusted certificate chain and expected server identity. A private CA may
be explicitly supplied. Self-signed devices need trust provisioned independently; no
trust-on-first-use, disabled hostname checks or automatic fingerprint learning is used.
`--certificate-sha256` is an additional check, not a replacement for chain validation:
it is compared only after the TLS handshake succeeds. A self-signed device therefore needs
its own certificate supplied as `--ca-file` together with the fingerprint; with the
fingerprint alone the check stops at `CERTIFICATE_VERIFY_FAILED` before any comparison,
which means "not trusted", not "device unreachable". The certificate must also pass
standard X.509 validation: its subject alternative name has to match `--server-name`, because
a certificate that only carries a common name is rejected.
Application-specific identity protocols are unsupported in this initial implementation.
A bare robot IP does not supply a service port; consult its documentation instead of
inventing a generic health endpoint.

`scan --wired` shows local wired inventory and cached observations only. Legacy subnet,
timeout and protocol-scan flags are rejected. Broad scanner helpers were retired, rather
than hidden behind another option. `worker doctor` defaults to local checks; explicit
`--runtime` and monitor sessions require configured TCP endpoints with bounded startup,
no multicast/gossip scouting/autoconnect and no unsolicited listeners. Unsupported SDK
configuration skips the session.

Deployment policy determines whether even one ordinary connection is permitted. Passive
mode remains usable when all active checks are disallowed. This is constrained opt-in
behavior, not universal firewall/NAC certification or invisibility to monitoring.
