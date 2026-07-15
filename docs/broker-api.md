# d2-broker HTTP API — reference for UI developers

A UI-focused companion to [broker.md](./broker.md). That document explains
why the broker exists and how it is operated; this one is the contract you
build a client against. Read it first if you are wiring up a menu bar app,
web dashboard, Electron/Tauri shell, or any other client that talks to
`d2-broker`.

The broker source is `bash-scripts-docker/d2-broker` (Python 3, stdlib
only); this document tracks what it actually does.

---

## 1. Connecting

| | |
|---|---|
| Base URL | `http://localhost:9300` (default; configurable via `D2_BROKER_PORT`) |
| Bind | `127.0.0.1` by default — loopback only. Same-machine clients only unless `D2_BROKER_BIND` is changed. |
| Transport | HTTP/1.1, JSON request and response bodies (`application/json`) |
| Auth | `Authorization: Bearer <token>` on every request **except** `GET /health` |
| Tokens | Two: `admin` (full access) and `agent` (restricted to `agent-*` instances). Stored in `$DHIS2_BASE/_broker/tokens.json`; print with `d2-broker tokens`. |

The broker treats request bodies as JSON regardless of `Content-Type`. You
can omit `Content-Type` and it will still parse. Bodies larger than 64 KiB
are rejected with `413`.

A UI built for end users should use the **admin** token. The **agent**
token is scoped down and exists for AI agent sandboxes; do not embed it
in a user-facing UI.

---

## 2. Error model

Errors return a JSON body:

```json
{ "error": "human-readable message" }
```

Status codes used by the broker:

| Code | Meaning |
|---|---|
| `200` | Success (synchronous GET) |
| `202` | Accepted — a job was queued; poll the `job.id` |
| `400` | Validation error (malformed body, bad name/version/seed) |
| `401` | Missing or invalid bearer token |
| `403` | Token lacks the required scope (e.g. agent token on non-agent instance, agent token requesting a backup seed or a `war_url`) |
| `404` | Instance, job, seed, or route not found |
| `405` | Method not allowed for this path |
| `409` | Conflict: instance already exists, instance has an active job, or agent cap reached |
| `413` | Request body > 64 KiB |
| `500` | Unhandled exception in the broker |

The broker never returns 5xx for predictable failures of the underlying
`d2-*` scripts — those become a job in state `failed` with `exit_code` and
an `error` message; fetch `/jobs/<id>/log` for the full output.

---

## 3. Job model

Anything that changes state on disk or Docker (create, reset, start, stop,
delete, backup, upgrade) returns **202 with a job**:

```json
{
  "job": { "id": "j-1a2b3c4d", "op": "create", "instance": "agent-test1",
           "status": "queued", "created_at": "2026-06-13T09:14:01+00:00",
           "started_at": null, "finished_at": null,
           "exit_code": null, "error": null, "result": null },
  "poll": "/jobs/j-1a2b3c4d",
  "log":  "/jobs/j-1a2b3c4d/log"
}
```

Status transitions:

```
queued ──► running ──► succeeded
                  └──► failed
                  └──► interrupted   (broker process died mid-job)
```

Terminal statuses: `succeeded`, `failed`, `interrupted`. Once `status`
flips to terminal, `finished_at`, `exit_code`, `error`, and (on success)
`result` are guaranteed to be populated. The broker writes the terminal
status only after all other fields are set, so polling clients never see
a half-written `succeeded`.

A single global worker processes the queue. Two POSTs to different
instances at the same time both succeed (both get `202`), but they run
sequentially. A second POST targeting an **instance that already has an
active job** gets `409`.

For `create`, `reset`, and `start`, the job's `result` on success is the
same shape as one element of `GET /instances` — UIs can use this to
refresh their cached instance without a follow-up call. `upgrade` likewise
returns a `GET /instances` element (with best-effort `dhis2_major_version`);
`backup` returns a `GET /seeds` element.

### Polling pattern (recommended)

1. POST to create/reset/start/stop/delete → grab `job.id`.
2. `GET /jobs/<id>` every 1–2 seconds.
3. Show `log_tail` (last 20 lines, plain text) to the user while
   `status` is `running`.
4. Stop polling when `status` is terminal.
5. On `failed` or `interrupted`, fetch `GET /jobs/<id>/log` for the full
   subprocess output.

---

## 4. Endpoints

### `GET /health`

Unauthenticated liveness probe. Returns `200`:

```json
{ "status": "ok", "service": "d2-broker" }
```

### `GET /instances?full=0|1`

List instances. Agent scope returns only `agent-*` instances. The optional
`full=1` adds `dhis2_major_version` (queries `flyway_schema_history` on
each running DB — slow if you have many instances; omit for the dashboard
list view, fetch per-instance on demand).

```json
{
  "instances": [
    {
      "name": "school-ind-test",
      "status": "running",
      "http_port": 9010,
      "pg_port": 5433,
      "localhost_url": "http://localhost:9010",
      "devnet_url": "http://dhis2-school-ind-test:8080",
      "devnet_db":  "dhis2-school-ind-test-db:5432",
      "agent_managed": false,
      "analytics": null,                 // "doris" if created with an analytics backend
      "dhis2_major_version": "42"        // only when full=1
    }
  ]
}
```

Field semantics:

| Field | Type | Notes |
|---|---|---|
| `name` | string | Lower-case, matches `^[a-z][a-z0-9_-]{1,29}$` |
| `status` | enum | `running` / `partial` / `stopped`. `partial` = one of tomcat/db running, the other not. |
| `http_port` | int \| null | Host port mapping for tomcat. `null` if container has no port published yet. |
| `pg_port` | int \| null | Host port mapping for Postgres. |
| `localhost_url` | string \| null | What to open in the user's browser. |
| `devnet_url` | string \| null | Tomcat URL inside the shared `dev-net` Docker network. `null` if not attached. |
| `devnet_db` | string \| null | Postgres `host:port` inside `dev-net` (creds always `dhis`/`dhis`/`dhis2`). |
| `agent_managed` | bool | `name` starts with `agent-`. UI can render an "agent" badge. |
| `analytics` | string \| null | `"doris"` if the instance was created with a dedicated analytics database, else `null`. Read from the instance `.env`, so it is accurate for stopped instances too. |
| `dhis2_major_version` | string \| null | e.g. `"42"`. Present only when `full=1`. |

### `POST /instances`

Create a new instance. Returns **202 + job**.

Body:

```json
{
  "name": "agent-test1",         // required
  "version": "2.42.4",           // optional; latest stable if you pass "42" or "2.42"
  "seed": "sl-demo-v42.sql.gz",  // optional; see Seed forms below
  "tomcat": "10",                // optional; "9"/"10". Auto-selected from version if omitted
  "memory": "4g",                // optional; Tomcat max heap (-Xmx), default 4g
  "http_port": 9010,             // optional; host HTTP port. Auto-selected (free) if omitted
  "pg_port": 5433,               // optional; host Postgres port. Auto-selected (free) if omitted
  "war_url":  "https://...",     // optional; admin only
  "war_file": "/abs/path.war",   // optional; admin only
  "analytics": "doris"           // optional; dedicated analytics DB, requires version >= 42
}
```

Validation:

- `name` matches `^[a-z][a-z0-9_-]{1,29}$`. Agent scope additionally
  requires `name` to start with `agent-`.
- `version` matches `^[0-9][0-9.]{0,15}$`. Major-only forms (`42`, `2.42`)
  resolve to the latest stable from `releases.dhis2.org` at job-run time.
- `tomcat` is the string `"9"` or `"10"`.
- `tomcat` is auto-selected from `version` when omitted (DHIS2 ≤ 2.41 → `9`,
  ≥ 2.42 → `10`). Passing a `tomcat` that conflicts with `version` fails the job.
- `memory` matches `^[0-9]+[mMgG]$` (e.g. `512m`, `2g`); default `4g` when
  omitted. Agent scope: heap above `D2_BROKER_MAX_AGENT_MEMORY` (default `8g`)
  → `400`.
- `http_port` / `pg_port` are integers `1024–65535` (else `400`). Omitted →
  the broker auto-selects a port not used or reserved by any other instance
  (running or stopped). An explicit port already in use/reserved fails the job.
- `war_url` must be `http://` or `https://`.
- `analytics` must be `"doris"` and requires a `version` with DHIS2 major
  ≥ 42 (→ `400` otherwise); combining it with `war_url`/`war_file` → `400`.
  The instance gets a per-instance Apache Doris container (~5.5 GB RAM).
- Agent scope:
  - `war_url` / `war_file` → `403`.
  - `seed` may only be a relative path inside `$DHIS2_BASE/_seeds/`.
  - At most `D2_BROKER_MAX_AGENT_INSTANCES` (default 5) `agent-*`
    instances; cap exceeded → `409`.
  - At most `D2_BROKER_MAX_AGENT_DORIS` (default 1) Doris-enabled
    instances; cap exceeded → `409`.
- An existing instance directory (`$DHIS2_BASE/<name>/`) or running
  container with the same name → `409`.

After the underlying create succeeds, two best-effort follow-ups run
(failure logged but not surfaced as a job failure):
1. `d2-dev-net-attach <name>` (idempotent re-attach of tomcat).
2. `docker network connect --alias dhis2-<name>-db dev-net <name>-db-1`
   — Postgres side-door on `dev-net`.

### `POST /instances/<name>/reset`

Restore the DB from a seed. Body:

```json
{ "seed": "sl-demo-v42.sql.gz" }   // required
```

To "reset to empty" you delete and re-create instead — `reset` always
takes a seed.

Returns **202 + job**. Returns **400** if the seed's filename carries a
`_vNN` version token (all backups made by the broker do, and the curated
seeds follow the convention) that is **newer** than the instance's current
DHIS2 major — restoring a newer database than the deployed WAR bricks the
instance (Flyway can't downgrade). Older seeds are fine; Flyway migrates
them up on next boot. The guard is skipped when either side is unknown
(no token in the filename, or the instance's DB is not running).

On restore the broker ensures a known `local_admin` / `district` superuser
(`ALL` authority) exists regardless of the restored database's own `admin`;
it is stripped from backups (`POST /instances/<name>/backup`), so dumps never
contain it. The same `local_admin` is ensured on create.

### `POST /instances/<name>/start` and `POST /instances/<name>/stop`

`docker compose up -d` / `docker compose down`. Body ignored. Returns
**202 + job**.

On `start`, the broker re-attaches the DB container to `dev-net` with the
`dhis2-<name>-db` alias (Tomcat's attachment lives in the compose file
and survives by itself; the DB's is a runtime attachment that does *not*
survive `docker compose down`).

### `DELETE /instances/<name>`

Stops, removes containers + volumes, deletes the instance directory.
Irreversible. Returns **202 + job**.

### `POST /instances/<name>/backup`

Create a `pg_dump` backup of the instance's database. **Admin only** (agent
token → `403`). Returns **202 + job**.

Body (all optional):

```json
{ "label": "pre-upgrade" }   // folded into the backup filename
```

- `label` matches `^[a-z0-9][a-z0-9_-]{0,39}$`.
- The DB must be running; on a stopped instance the job `fail`s with a
  "start the instance first" message.

On success, `result` is a `GET /seeds`-shaped element for the new backup, so
it can be offered immediately as a restore source:

```json
{
  "path": "backups/<name>/<name>_20260614-091401_v42.sql.gz",
  "source": "backups",
  "size_bytes": 928374829,
  "modified": "2026-06-14T09:14:01+00:00"
}
```

### `POST /instances/<name>/upgrade`

Swap the running WAR (version bump or specific WAR), preserving the DB and
volumes. Returns **202 + job**.

Body (exactly one of `version` / `war_url` / `war_file` required):

```json
{
  "version":  "2.42.4",
  "war_url":  "https://…/dhis.war",   // admin only
  "war_file": "/abs/path.war",        // admin only
  "tomcat":   "10",                   // optional
  "backup_first": true                // optional, default true
}
```

- `version` matches `^[0-9][0-9.]{0,15}$`; major-only resolves at job-run time.
- Version transitions: downgrades and major-version skips are rejected with
  `400`; same-major and one-major-up are allowed. (Not enforced for
  `war_url`/`war_file`, whose version can't be read in advance.)
- `tomcat` (`"9"`/`"10"`): changing the servlet container is **not yet
  supported** — a value differing from the instance's current Tomcat (or that
  can't be matched) → `400`.
- `backup_first` (default `true`) prepends a `d2-db-backup` step; its path is
  written to the job log.

On success, `result` is the `GET /instances` element with a best-effort
`dhis2_major_version`. Because Flyway migrates asynchronously on Tomcat boot,
this may still read the pre-upgrade major; re-poll `GET /instances?full=1`
once the instance is back up to observe the migrated version.

### `POST /instances/<name>/memory`

Set the Tomcat max heap (`-Xmx`) on an existing instance and recreate the
Tomcat container (DB and volumes preserved). Returns **202 + job**.

Body:

```json
{ "memory": "2g" }   // required; matches ^[0-9]+[mMgG]$
```

- `memory` is required; `^[0-9]+[mMgG]$` (e.g. `512m`, `2g`).
- Agent scope: heap above `D2_BROKER_MAX_AGENT_MEMORY` (default `8g`) → `400`.
- `result` on success is the `GET /instances` element (like `start`). The
  instance restarts as Tomcat is recreated.

### `GET /seeds`

```json
{
  "seeds_dir": "/Users/olavpo/dhis2/_seeds",
  "seeds": [
    {
      "path": "sl-demo-v42.sql.gz",
      "source": "seeds",
      "size_bytes": 184729281,
      "modified": "2026-04-01T13:22:08+00:00"
    },
    {
      "path": "backups/acdc/acdc_2026-03-03.sql.gz",      // admin only
      "source": "backups",
      "size_bytes": 928374829,
      "modified": "2026-03-03T11:00:00+00:00"
    }
  ]
}
```

Agent scope sees only `source: "seeds"` entries; admin scope additionally
sees backups (prefixed `backups/`).

### `GET /jobs`

```json
{ "jobs": [<job>, ...] }
```

Up to 50 most recent jobs, newest first. Agent scope filters to jobs
whose `instance` starts with `agent-`.

### `GET /jobs/<id>`

```json
{
  "id": "j-1a2b3c4d",
  "op": "create",                        // create | reset | start | stop | delete | backup | upgrade | memory
  "instance": "agent-test1",
  "status": "running",                   // queued | running | succeeded | failed | interrupted
  "created_at":  "2026-06-13T09:14:01+00:00",
  "started_at":  "2026-06-13T09:14:02+00:00",
  "finished_at": null,
  "exit_code":   null,
  "error":       null,
  "result":      null,                   // on success: GET /instances element (create/reset/start/upgrade) or GET /seeds element (backup)
  "log_tail":    "...last 20 lines of subprocess output..."
}
```

`log_tail` is appended to the job object on the single-job endpoint only
(not on `GET /jobs`). On `succeeded`, `result` for `create` / `reset` /
`start` / `upgrade` is the same shape as a `GET /instances` element
(`upgrade` adds a best-effort `dhis2_major_version`); for `backup` it is a
`GET /seeds` element.

### `GET /jobs/<id>/log`

Plain `text/plain` body, up to 10 000 lines of the subprocess stdout/
stderr stream. Useful as a "show full log" affordance when a job fails.

---

## 5. Seed forms (quick reference)

| Form | Example | Available to |
|---|---|---|
| Relative path under `_seeds/` | `"sl-demo-v42.sql.gz"` | agent + admin |
| Path under `_backups/` (prefix `backups/`) | `"backups/acdc/2026-03.sql.gz"` | admin only |
| Absolute filesystem path | `"/tmp/dump.sql.gz"` | admin only |
| HTTP(S) URL | `"https://databases.dhis2.org/.../sl.sql.gz"` | admin only |

URL seeds are downloaded by the job worker (a `curl` step prefixed to the
job) and removed once the job finishes. The URL's last path component
must end in `.sql`, `.sql.gz`, or `.pgc` so the restore method can be
detected from the suffix.

---

## 6. Validation cheat-sheet

| Field | Rule |
|---|---|
| Instance `name` | `^[a-z][a-z0-9_-]{1,29}$` (2–30 chars, starts lower-case) |
| Agent-scope `name` | additionally must start with `agent-` |
| `version` | `^[0-9][0-9.]{0,15}$` (e.g. `42`, `2.42`, `2.42.4`) |
| `tomcat` | exactly `"9"` or `"10"` (strings, not ints) |
| `war_url` | starts with `http://` or `https://` |
| Seed filename | ends with `.sql`, `.sql.gz`, or `.pgc` |
| `label` (backup) | `^[a-z0-9][a-z0-9_-]{0,39}$` |
| `backup_first` | boolean, default `true` |
| `memory` | `^[0-9]+[mMgG]$` (e.g. `512m`, `2g`); agent heap capped by `D2_BROKER_MAX_AGENT_MEMORY` (default `8g`) |

Show validation errors from the broker verbatim — they are concise and
already user-facing.

---

## 7. Suggested UI mappings

For an instance card:

- **Title** = `name`
- **State pill** = colour by `status` (running = green, partial = amber, stopped = grey)
- **"DHIS2 vX" badge** = from `dhis2_major_version` (issue a `?full=1`
  refresh or per-instance fetch when the user opens the card; skip on
  the list view to keep it snappy)
- **"Open in browser" link** = `localhost_url`
- **"Reachable from sandboxes as" hint** = `devnet_url` (small print)
- **"DB connection" hint** = `dhis: dhis@localhost:<pg_port>/dhis2` for
  host tools, or `dhis2-<name>-db:5432` for containers on dev-net (from
  `devnet_db`)
- **"Managed by AI agent" badge** = `agent_managed`
- **Actions** = Start (if `stopped`), Stop (if `running`), Reset
  (always — opens a "pick a seed" dialog populated by `GET /seeds`),
  Delete (always — confirm).

For the activity drawer / job feed:

- Show queued/running jobs prominently with a streaming `log_tail`
  preview (re-fetch the job every 1–2 s).
- When a job finishes, animate it into a "recent activity" list. Show
  the friendly `op` ("Created `agent-test1`") and link to `GET /jobs/<id>/log`
  for diagnostics on failure.
- A single "active operation" indicator at the top is appropriate
  because the broker serializes globally — there is never more than one
  job actually doing work.

For creation:

- Leave `tomcat` unset when you pass a `version` — the broker auto-selects the
  compatible Tomcat. Only set it for the no-version (empty-instance) case.
- Show recent versions as suggestions but allow free-text entry (because
  major-only resolves at job-run time).
- Show available seeds from `GET /seeds`; include "No seed (empty
  database)" as an option for create.

---

## 8. Examples (curl)

```bash
TOKEN=$(python3 -c 'import json;print(json.load(open("'$DHIS2_BASE'/_broker/tokens.json"))["admin"]["token"])')
B=http://localhost:9300
H="Authorization: Bearer $TOKEN"

# List instances
curl -s -H "$H" "$B/instances?full=1" | jq .

# Create an empty 2.42 instance
curl -s -X POST -H "$H" -d '{"name":"demo1","version":"2.42"}' "$B/instances" | jq .

# Watch a job until terminal
job=j-1a2b3c4d
while true; do
  s=$(curl -s -H "$H" "$B/jobs/$job" | jq -r .status)
  echo "$s"
  case "$s" in succeeded|failed|interrupted) break ;; esac
  sleep 1
done
curl -s -H "$H" "$B/jobs/$job/log" | tail -50

# Reset from a curated seed
curl -s -X POST -H "$H" -d '{"seed":"sl-demo-v42.sql.gz"}' \
  "$B/instances/demo1/reset" | jq .

# Stop, start, delete
curl -s -X POST   -H "$H" "$B/instances/demo1/stop"
curl -s -X POST   -H "$H" "$B/instances/demo1/start"
curl -s -X DELETE -H "$H" "$B/instances/demo1"
```

---

## 9. Things the API deliberately does **not** do

- **No streaming.** No SSE, no WebSocket. Poll the job endpoint.
  `log_tail` is the last 20 lines of stdout; render that as the live
  view, full log on demand from `/jobs/<id>/log`.
- **No bulk operations.** One instance per request. The broker
  serializes globally anyway.
- **No arbitrary command execution.** Only the fixed verb set above. A
  UI cannot smuggle through extra `docker` or `d2-*` calls — if you
  need them, run them from the host side.
- **No CORS configuration.** Loopback-only; intended for clients on the
  same machine (a native shell or a localhost-served web app). If you
  serve a browser UI from a different origin, add a CORS-permissive
  reverse proxy in front; do not expose the broker directly to a
  remote origin.
- **No token issuance over HTTP.** Tokens are produced on the box by
  `d2-broker tokens` / generated on first start; users paste them into
  the UI's settings.
