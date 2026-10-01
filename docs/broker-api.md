# d2-broker HTTP API — reference for UI developers

A UI-focused companion to [broker.md](./broker.md). That document explains
why the broker exists and how it is operated; this one is the contract you
build a client against. Read it first if you are wiring up a menu bar app,
web dashboard, Electron/Tauri shell, or any other client that talks to
`d2-broker`. (For MCP clients like Claude Desktop, a ready-made adapter
exists: [broker-mcp.md](./broker-mcp.md).)

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
| `409` | Conflict: instance already exists, instance has an active job (delete is exempt — see `DELETE /instances/<name>`), or agent cap reached |
| `413` | Request body > 64 KiB |
| `500` | Unhandled exception in the broker |

The broker never returns 5xx for predictable failures of the underlying
`d2-*` scripts — those become a job in state `failed` with `exit_code` and
an `error` message; fetch `/jobs/<id>/log` for the full output.

---

## 3. Job model

Anything that changes state on disk or Docker (create, reset, start, stop,
delete, backup, upgrade, memory, restart-db, analytics) returns **202 with a
job**:

```json
{
  "job": { "id": "j-1a2b3c4d", "op": "create", "instance": "agent-test1",
           "status": "queued", "created_at": "2026-06-13T09:14:01+00:00",
           "started_at": null, "finished_at": null,
           "exit_code": null, "error": null, "result": null,
           "requested_by": { "scope": "agent", "client": "sbx-tool-box",
                             "user_agent": "curl/8.5.0" } },
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

`requested_by` records who submitted the job: the token scope, the optional
`X-D2-Client` request header (sandbox clients should send their hostname)
and the `User-Agent` (`d2-broker-mcp` sends its pid and parent pid). The
broker also writes one `[job]` line per submitted job to `broker.log`, so a
deleted instance can be traced to a client.

An `interrupted` job carries an `error` that says what to do: a job that was
still queued never ran and can simply be submitted again; a job that was
running may have left the instance half-done (delete or reset it). Restart
the broker with `d2-broker restart`, which waits for the queue to empty,
rather than a bare `launchctl kickstart`.

Terminal statuses: `succeeded`, `failed`, `interrupted`. Once `status`
flips to terminal, `finished_at`, `exit_code`, `error`, and (on success)
`result` are guaranteed to be populated. The broker writes the terminal
status only after all other fields are set, so polling clients never see
a half-written `succeeded`.

A single global worker processes the queue. Two POSTs to different
instances at the same time both succeed (both get `202`), but they run
sequentially. A second POST targeting an **instance that already has an
active job** gets `409` — except a **delete**, which queues behind the
active job and runs after it (a second delete on the same instance
still gets `409`).

Jobs that boot the app (`create`, `reset`, `start`, `upgrade`, `memory`)
stay `running` until the instance's API answers as `ready` — not merely
until the containers are up. This is deliberate: because the worker is
serialized, holding here keeps the next instance from starting its heavy
Flyway boot while this one is still migrating (the two would starve each
other). If the instance isn't ready within `D2_BROKER_BOOT_WAIT` seconds
(default 1800) the job succeeds anyway, with a log line noting it may still
be migrating. Expect these jobs to run for minutes on a fresh DB — show the
`log_tail`; it streams migration progress.

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
each running DB), `readiness` (probes `/api/system/info` on each running
instance) and `containers` (restart and OOM facts from `docker inspect`) —
slow if you have many instances; omit for the dashboard list view, fetch
per-instance on demand.

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
      "created_at": "2026-07-16T08:12:44+00:00",
      "label": "PI-mapper review",       // free text from create; null if none
      "timezone": "Africa/Lagos",        // from create; null = Etc/UTC or predates the field
      "dhis2_major_version": "42",       // only when full=1
      "readiness": "ready",              // only when full=1
      "containers": {                    // only when full=1; null for a missing container
        "tomcat": { "restart_count": 0, "oom_killed": false,
                    "started_at": "2026-10-01T08:16:07.38Z", "last_exit_code": 0 },
        "db":     { "restart_count": 0, "oom_killed": false,
                    "started_at": "2026-10-01T08:15:20.12Z", "last_exit_code": 0 }
      }
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
| `created_at` | string \| null | UTC ISO timestamp of the create request. `null` for instances predating this field (no meta file). |
| `label` | string \| null | Free-text label passed on create — lets concurrent sessions recognise their own instances. |
| `timezone` | string \| null | IANA time zone passed on create. `null` means the default, `Etc/UTC`. |
| `dhis2_major_version` | string \| null | e.g. `"42"`. Present only when `full=1`. |
| `readiness` | enum \| null | Present only when `full=1`. `deploying` (no HTTP answer on `/api` yet) / `migrating` (HTTP answers, no version yet — Flyway/context startup) / `ready` (`/api/system/info` 200 with a version, or 401/403) / `null` (not running or no published port). Best-effort probe with a 3 s timeout, authenticated as the built-in `local_admin` superuser; if that answers 5xx, an anonymous `/api/system/ping` returning 200 also counts as `ready` (a broken `local_admin` row must not look like an endless migration). **`ready` means metadata-usable, not that analytics/resource tables are built** — don't trigger analytics off this signal. If you need the earliest possible "usable" moment, probe the instance's API directly (an authenticated `/api/system/info` or a real metadata query answers before some of the boot settles). |
| `containers` | object | Present only when `full=1`. Per role (`tomcat`, `db`, plus `doris` when enabled): `restart_count` (restarts by the `restart: always` policy, i.e. a crashed or OOM-killed process; `docker start` does not count), `oom_killed`, `started_at` (moves forward when the process came back) and `last_exit_code`. A Tomcat restart wipes DHIS2's in-memory job notifications, so a running analytics job then looks stuck; check here first. |

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
  "analytics": "doris",          // optional; dedicated analytics DB, requires version >= 42
  "timezone": "Africa/Lagos",    // optional; server time zone, default Etc/UTC
  "cors_origins": ["http://localhost:4000"],  // optional; added to the CORS allowlist
  "label": "PI-mapper review"    // optional; free text (<= 100 chars), echoed in GET /instances
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
- `label` is free text, at most 100 printable characters (→ `400` otherwise).
  Stored broker-side (`_broker/meta/`) with the creation timestamp.
- `timezone` is an IANA name present in the host's zoneinfo, e.g.
  `Africa/Lagos` (→ `400` otherwise). It sets `TZ` for Tomcat and Postgres and
  `-Duser.timezone` for the JVM. DHIS2's "cannot be a future date" checks use
  the server's date, so an instance standing in for a UTC+1 deployment
  should run on UTC+1, or device syncs fail after local midnight.
- `cors_origins` is a list of at most 20 origins like
  `http://localhost:3000` (scheme, host, optional port; → `400` otherwise).
- Agent scope:
  - `war_url` / `war_file` → `403`.
  - `seed` may only be a relative path inside `$DHIS2_BASE/_seeds/`.
  - At most `D2_BROKER_MAX_AGENT_INSTANCES` (default 5) `agent-*`
    instances; cap exceeded → `409`.
  - At most `D2_BROKER_MAX_AGENT_DORIS` (default 1) Doris-enabled
    instances; cap exceeded → `409`.
- An existing instance directory (`$DHIS2_BASE/<name>/`) or running
  container with the same name → `409`.
- The cap error lists every `agent-*` instance with its status, stopped and
  partial ones first: `agent instance cap reached (5); stopped instances
  count too. Delete one of: agent-a (stopped), agent-b (running), …`.

The compose template puts both containers on `dev-net` from the start
(`dhis2-<name>` for Tomcat, `dhis2-<name>-db` for Postgres), so the database
resolves while the create is still running.

After the underlying create succeeds, best-effort follow-ups run (failure
logged but not surfaced as a job failure):
1. `d2-dev-net-attach <name>` — idempotent; only does something for
   instances whose installed template predates the dev-net blocks.
2. Once the API answers: one anonymous GET to a legacy page
   (`[warm-up]` in the job log). On 2.41, `/api/authorities` and similar
   endpoints answer 500 "Struts Dispatcher.getInstance() is null" until the
   legacy web layer has loaded once. The same warm-up runs after reset,
   start, upgrade and memory.
3. Once the API answers: the CORS allowlist gets `http://localhost:3000`,
   `http://localhost:8080` and any `cors_origins`, merged with what the
   database already allowed (`[cors]` line in the job log). Without it a
   `d2-app-scripts` dev-server login returns an empty 200 and no session.

On a blank create (`version`, no `seed`) the job also confirms that
`local_admin` can log in before it finishes.

**A create that fails is cleaned up**: the broker deletes the half-made
instance (containers, volumes, directory) so it doesn't hold a slot under
the agent cap. The job log keeps the create output, the tail of the
database restore log, and the delete output.

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

After a reset the broker re-applies the CORS allowlist (defaults plus the
instance's `cors_origins` from create), since the restored database brings
its own.

### `POST /instances/<name>/start` and `POST /instances/<name>/stop`

`docker compose up -d` / `docker compose down`. Body ignored. Returns
**202 + job**.

On `start`, the broker runs `d2-dev-net-attach`, which re-attaches the DB
container for instances created before the DB's dev-net block was in the
compose template (their attachment did not survive `docker compose down`),
then warms the legacy web layer once the API answers.

### `DELETE /instances/<name>`

Stops, removes containers + volumes, deletes the instance directory.
Irreversible. Returns **202 + job**.

Unlike the other mutations, a delete is accepted even while the instance
has an active job: it queues behind it and runs once that job finishes
(useful because start/create jobs hold for minutes waiting on boot). A
delete is only rejected with `409` when a delete for the instance is
already queued or running. If the instance is already gone when the delete
runs (e.g. it was queued behind a create that failed and was cleaned up),
the delete succeeds with a note in its log.

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
- Analytics tables (`analytics`, `analytics_*`) are excluded from the dump, so
  an instance restored from this backup has no analytics until you run it.

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

An upgrade from 2.41 to 2.42 also moves the instance from Tomcat 9 to Tomcat
10 (DHIS2 2.42 needs jakarta servlets): a `d2-switch-tomcat` step
regenerates the compose file from the Tomcat 10 template with the same
ports, heap and time zone, keeps the database volume, and then the new WAR
is deployed and Flyway migrates the database on boot. The previous compose
file is kept as `docker-compose.yml.tomcat9.bak`.

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
- `tomcat` (`"9"`/`"10"`): only accepted when it equals the instance's
  current Tomcat (else `400`). The 9 → 10 move for 2.41 → 2.42 happens
  automatically from `version`; leave `tomcat` out.
- `backup_first` (default `true`) prepends a `d2-db-backup` step; its path is
  written to the job log.

On success, `result` is the `GET /instances` element with `dhis2_major_version`.
The job holds until the upgraded instance is `ready` (see the boot-gate note in
§3) before reading it, so it reflects the migrated schema rather than the
pre-upgrade major.

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

### `POST /instances/<name>/restart-db`

Restart only the Postgres container — seconds, against minutes for a full
stop/start — e.g. after `ALTER SYSTEM` for a setting that needs a server
restart. Body ignored. Returns **202 + job**. Tomcat keeps running and its
connection pool reconnects, so the first request afterwards may fail — even
as a 401, because the login lookup hits a dead pooled connection. Not a booting job: it finishes once Postgres accepts connections.

### `POST /instances/<name>/analytics`

Generate DHIS2 analytics tables and wait for the run to finish. Returns
**202 + job**.

```json
{ "last_years": 2 }   // optional; integer 1–50, counted back from today. Omit for all years
```

The job (`d2-analytics`) calls `POST /api/resourceTables/analytics` as
`local_admin`, copies DHIS2's task notifications into the job log, and
succeeds only when DHIS2 reports completion without an error. It fails
rather than hanging when the notifications vanish mid-run (Tomcat
restarted), when nothing starts within 10 minutes (typically a stale
`RUNNING` analytics job left by an earlier restart; the log says how to
clear it), or after ~6900 s. Like every job it runs on the single worker, so
it never overlaps another instance's restore, boot or analytics run — and
other jobs queue behind it.

### `GET /seeds`

```json
{
  "seeds_dir": "/Users/olavpo/dhis2/_seeds",
  "seeds": [
    {
      "path": "sl-demo-v42.sql.gz",
      "source": "seeds",
      "size_bytes": 184729281,
      "modified": "2026-04-01T13:22:08+00:00",
      "dhis2_version": 42,                                 // from the _vNN filename token; null if absent
      "notes": {                                           // from <file>.json next to the dump; null if absent
        "description": "Sierra Leone demo database (DHIS2 2.42).",
        "admin_user": "admin logs in but is not a superuser. Use local_admin/district.",
        "known_issues": []
      }
    },
    {
      "path": "backups/acdc/acdc_2026-03-03.sql.gz",      // admin only
      "source": "backups",
      "size_bytes": 928374829,
      "modified": "2026-03-03T11:00:00+00:00",
      "dhis2_version": null,
      "notes": null
    }
  ]
}
```

`notes` is a hand-written sidecar: a JSON object in a file named after the
dump plus `.json` (e.g. `lao_hmis_demo_v42.sql.gz.json`), returned as-is.
Conventional keys: `description`, `data_until` (how current the data is),
`admin_user` (whether the seed's own `admin` works), `analytics_ok`,
`known_issues` (list of strings). Read them before provisioning: they say
which seeds are metadata-only, which have a disabled `admin`, and which
carry known defects.

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
  "op": "create",                        // create | reset | start | stop | delete | backup | upgrade | memory | restart-db | analytics
  "instance": "agent-test1",
  "status": "running",                   // queued | running | succeeded | failed | interrupted
  "created_at":  "2026-06-13T09:14:01+00:00",
  "started_at":  "2026-06-13T09:14:02+00:00",
  "finished_at": null,
  "exit_code":   null,
  "error":       null,
  "result":      null,                   // on success: GET /instances element (create/reset/start/upgrade) or GET /seeds element (backup)
  "requested_by": { "scope": "agent", "client": null, "user_agent": "curl/8.5.0" },
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
| `timezone` | IANA name in the host zoneinfo, e.g. `Africa/Lagos` |
| `cors_origins` | list of ≤ 20 `http(s)://host[:port]` origins |
| `last_years` (analytics) | integer 1–50 |

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
