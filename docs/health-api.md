# Health and host resources

`GET /healthz` without credentials keeps the public deployment/liveness contract:

```json
{"status": "ok"}
```

For host resources send the configured personal owner's current Supabase access
token. Other authenticated users receive 403; invalid/expired tokens receive
401. Without a configured `personal_user_id`, detailed metrics are denied.

```http
GET /healthz
Authorization: Bearer <supabase-access-token>
```

Example after CPU sampling has warmed up (numbers are illustrative):

```json
{
  "status": "ok",
  "version": "0.8.0",
  "checks": {"api": "ok"},
  "resources": {
    "scope": "host",
    "sampled_at": "2026-11-19T12:00:00+00:00",
    "status": "ok",
    "cpu": {
      "status": "ok",
      "used_percent": 32.5,
      "logical_cores": 2,
      "sample_window_seconds": 10.0
    },
    "memory": {
      "status": "ok",
      "used_percent": 50.0,
      "total_bytes": 4294967296,
      "available_bytes": 2147483648,
      "used_bytes": 2147483648
    },
    "uptime_seconds": 86400.0
  }
}
```

Poll every 10–15 seconds while the monitoring screen is open. Reuse the app's
normal session refresh; do not hardcode a permanent token. Responses use
`Cache-Control: no-store`. Detailed requests use the existing authentication
and API throttling path.

- CPU is average busy time across all host logical CPUs, on a 0–100 scale.
  It uses deltas of aggregate `/proc/stat` counters, excluding idle and I/O wait.
  The first detailed request establishes a baseline and returns `null`/`unknown`
  for CPU. Further samples need at least one second; more frequent calls reuse
  the latest CPU value. `sample_window_seconds` describes that measurement window.
- Memory uses `MemTotal - MemAvailable`, so reclaimable cache is not treated as
  permanently unavailable RAM. Values are bytes, not MB. Convert for display as
  desired; percentages are already calculated.
- `uptime_seconds` is host uptime, not API container uptime.
- Missing/unreadable counters return null values and `unknown`, not a fabricated
  zero or an endpoint failure. Non-Linux local runs may have no resource counters.
- CPU warning/critical thresholds are 90%/98%; memory thresholds are 85%/95%.
  Aggregate resource status prioritizes critical, warning, unknown, then ok.
- Top-level `status: ok` means the API answered. Resource warnings do not change
  the public liveness contract. This endpoint does not verify worker/maintenance
  activity, database health, ChatGPT sessions, model availability, disk health,
  EC2 CPU credits or AWS instance/system status checks.

## Deployment

`compose.personal.yml` mounts only the host's aggregate `/proc/stat`,
`/proc/meminfo` and `/proc/uptime` files read-only under `/run/host` in the API
container and sets `LUCRARI_HOST_PROC_DIR=/run/host`. It does not mount the full
host `/proc`, expose process data, or grant Docker socket/privileged access.
With that configuration `scope` is `host`, covering all EC2 workloads rather than
just the API container. Without it the default is `/proc` with `scope: system`;
do not label that as verified EC2-host data in the frontend.

The image and updated Compose configuration both need deployment for these
changes to work on EC2. No deployment is performed by this change. A stopped or
unreachable EC2 cannot answer this endpoint; the frontend should show an
unreachable state on request failure instead of displaying old numbers as live.
