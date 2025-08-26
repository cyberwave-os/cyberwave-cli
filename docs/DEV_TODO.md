## Cyberwave CLI — TODO (Developer Value Prop)

Legend: ✅ implemented · 🟡 limited · ❌ not implemented

### Feature Matrix

| Area | Feature | Status | Notes |
|---|---|---|---|
| Sensors | Tail analyzer events (backend) | ✅ | `cyberwave sensors events --sensor <UUID>` polls `/sensors/{uuid}/events` |
| Sensors | Tail analyzer events (node/session fallback) | ✅ | `--environment <ENV_UUID>` falls back to session NDJSON |
| Nodes | Node identity bootstrap (register/claim) | ❌ | `nodes register` and `nodes claim` (see user stories) |
| Nodes | Node config bootstrap (edge.json write) | ❌ | `nodes init` to persist `node_uuid` + `device_token` |
| Nodes | Heartbeat | ❌ | `nodes heartbeat` (manual) + auto heartbeat in edge runner |
| Nodes | Token rotation | ❌ | `nodes rotate-token` and server-driven rotate via heartbeat response |
| Nodes | Revoke | ❌ | `nodes revoke --node <UUID>` admin op |
| Environments | Quick list/get helpers | 🟡 | Add simple filters and formatting |
| Twins | Simple command send | ✅ | `cyberwave twins send-command` |
| Edge | Simulate camera | ✅ | `cyberwave edge simulate --sensor <UUID> --video file.mp4` |

### User Stories

- ❌ As an operator, I can register a new node from the cloud: `cyberwave nodes register --project <UUID> --name my-node` → prints `node_uuid` and writes `~/.cyberwave/edge.json` with `device_token`.
- ❌ As an operator, I can claim-pair a node using a short code: `cyberwave nodes claim --code ABCD-1234` → persists `node_uuid` + `device_token`.
- ❌ As an operator, I can rotate a node token: `cyberwave nodes rotate-token --node <UUID>`.
- ❌ As an operator, I can view node status and last heartbeat: `cyberwave nodes status --node <UUID>`.
- ✅ As a developer, I can tail sensor analyzer events from backend quickly: `cyberwave sensors events --sensor <UUID>`.
- ✅ As a developer, if backend events are unavailable, I can fallback to session logs: `cyberwave sensors events --sensor <UUID> --environment <ENV_UUID>`.

### API Sketches (Backend alignment)

- POST `/api/v1/nodes/register` → body: `{ name, project_uuid, hardware_id?, public_key? }` → `{ node_uuid, device_token, key_id? }`
- POST `/api/v1/nodes/claim` → `{ claim_code, public_key? }` → `{ node_uuid, device_token, key_id? }`
- POST `/api/v1/nodes/heartbeat` → `{ node_uuid, version, uptime_s, caps? }` → `{ rotate_token?: true, device_token?: "..." }`

### CLI Commands (proposed)

- `cyberwave nodes init --project <UUID> --name <NAME> [--claim <CODE>] [--use-device-keypair]`
- `cyberwave nodes register --project <UUID> --name <NAME> [--hardware-id <ID>]`
- `cyberwave nodes claim --code <CLAIM_CODE>`
- `cyberwave nodes heartbeat --node <UUID>`
- `cyberwave nodes rotate-token --node <UUID>`
- `cyberwave nodes status --node <UUID>`


