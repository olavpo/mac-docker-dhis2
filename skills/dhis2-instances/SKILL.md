---
name: dhis2-instances
description: Create, reset, start/stop and delete disposable DHIS2 test instances on the host machine via the d2-broker HTTP API. Use whenever a task needs a live DHIS2 instance to test against — trying out metadata imports, indicators, apps, or API behavior — and no suitable instance is reachable, or the current test instance needs a clean state. Triggers on phrases like "spin up a DHIS2 instance", "test this on a clean DHIS2", "reset the test database", "I need DHIS2 2.42 to verify this". Only works when DHIS2_BROKER_URL and DHIS2_BROKER_TOKEN are set (typically inside an agent sandbox); if they are unset, this capability is unavailable — ask the user instead.
---

# DHIS2 test instances via d2-broker

The host machine runs `d2-broker`, an HTTP API for managing DHIS2 Docker
instances. You can request disposable test instances without host access.

**Connection** (both env vars must be set, otherwise stop and ask the user):

```bash
curl -s -H "Authorization: Bearer $DHIS2_BROKER_TOKEN" "$DHIS2_BROKER_URL/instances"
```

## What you can and cannot do

- You manage instances named **`agent-*` only**. Always pick names like
  `agent-<purpose>` (e.g. `agent-indicator-test`). Other instances on the
  host are visible to the user only — never assume you can touch them.
- Seeds: **only the curated list from `GET /seeds`** (demo/test databases the
  user has approved for agent use), or **no seed at all** for a clean, empty
  DHIS2 that initializes itself on first start. You cannot restore real
  backups, host files, or URLs — don't ask the broker to; it will refuse.
- There is a cap on concurrent `agent-*` instances — it counts **stopped
  instances too**, including stale leftovers from earlier sessions. When the
  cap is hit, the error names deletion candidates; delete only `agent-*`
  ones. In practice, keep the live footprint to **1 running instance where
  possible, 2 max**: two DHIS2 instances *booting* concurrently starve each
  other (20+ minutes with nothing on `/api`). If you need a second, wait
  until the first answers `/api/system/info` before creating it.
- **Backups are admin-only**: `POST /instances/<name>/backup` returns `403`
  for your token (`_backups/` is the user's territory). To get clean or known
  state, reset from a seed or delete and re-create — don't try to back up.
- **Guaranteed superuser:** every instance you create or reset has a
  `local_admin` / `district` superuser (`ALL` authority), added by the host
  tooling regardless of the restored database's own `admin`. Prefer it when a
  seed's `admin` is disabled or has an unknown password. It's stripped from
  backups, so it never appears in dumps.
- **Don't set `tomcat` when you pass a `version`** — the broker auto-selects the
  compatible Tomcat (DHIS2 ≤ 2.41 needs Tomcat 9, ≥ 2.42 needs Tomcat 10). A
  conflicting `tomcat` makes the create job fail.
- Ports: you normally reach instances via `devnet_url`, so you rarely need host
  ports. If you do, pass `http_port`/`pg_port` (integers 1024–65535) on create;
  omit them to let the broker pick free, non-colliding ports.

## Operations

All mutating calls return **202 with a job**; poll until terminal status.
(Request bodies are parsed as JSON regardless of the Content-Type header,
so plain `curl -d` works too — don't "fix" calls that omit the header.)

```bash
B="$DHIS2_BROKER_URL"
H="Authorization: Bearer $DHIS2_BROKER_TOKEN"
CT="Content-Type: application/json"

# What exists / what seeds are available
curl -s -H "$H" $B/instances
curl -s -H "$H" $B/seeds

# Create
curl -s -X POST -H "$H" -H "$CT" -d '{"name":"agent-mytest","version":"42"}' $B/instances
curl -s -X POST -H "$H" -H "$CT" -d '{"name":"agent-mytest","version":"2.42.4","seed":"<path from /seeds>"}' $B/instances

# Poll the job (id from the 202 response)
curl -s -H "$H" $B/jobs/<job-id>          # status + log_tail + result
curl -s -H "$H" $B/jobs/<job-id>/log      # full log, plain text

# Reset the database to a known seed state (Tomcat restarts automatically)
curl -s -X POST -H "$H" -H "$CT" -d '{"seed":"<path from /seeds>"}' $B/instances/agent-mytest/reset

# Start / stop / delete
curl -s -X POST -H "$H" $B/instances/agent-mytest/start
curl -s -X POST -H "$H" $B/instances/agent-mytest/stop
curl -s -X DELETE -H "$H" $B/instances/agent-mytest

# Upgrade in place — swap the WAR, keep the database (see note below)
curl -s -X POST -H "$H" -H "$CT" -d '{"version":"2.42"}' $B/instances/agent-mytest/upgrade

# Change the Tomcat heap (recreates Tomcat; DB preserved)
curl -s -X POST -H "$H" -H "$CT" -d '{"memory":"2g"}' $B/instances/agent-mytest/memory
```

**`version` accepts:** a major (`"42"` or `"2.42"` → latest stable of that
major, resolved from releases.dhis2.org) or an exact release (`"2.42.4"`,
`"42.4"` also works). Omit it for no DHIS2 at all.

**The three creation states** — pick deliberately:

| Body | Result |
|---|---|
| neither `version` nor `seed` | Tomcat + empty Postgres, **no DHIS2 deployed** — a shell, nothing answers on `/api` |
| `version` only | That DHIS2 release on an empty database (Flyway initializes it; no org units, no metadata, only the admin user) |
| `version` + `seed` | That release on the seeded database — the usual choice for testing |

`seed` without `version` is valid but rarely useful: the database is
restored but no DHIS2 webapp is deployed to serve it.

**Upgrading an instance** (`POST /instances/<name>/upgrade`): swaps the WAR
while keeping the database and volumes, then lets Flyway migrate on boot.
**Only reach for this when the task is specifically about an upgrade/migration
path** (e.g. "does this metadata survive a 2.41 → 2.42 upgrade"). If you just
need an instance at version X, create one at X — don't create-then-upgrade.
For your token: only the `version` form works (`war_url`/`war_file` are
admin-only → 403), and only same-major or one-major-up is allowed (downgrades
and skips → 400). **`/upgrade` cannot cross the 2.41 → 2.42 boundary**: instances created at
≤ 2.41 run Tomcat 9, but 2.42+ needs Tomcat 10, and `/upgrade` swaps only the
WAR, not Tomcat. The broker **refuses this crossing with a `400`** (deploying a
2.42 WAR onto the old Tomcat 9 would 404 everywhere). To move a database across
that boundary, **create a fresh instance at the target version with the older
seed** and let Flyway migrate it on boot (a v41 seed on a 2.42/2.43 instance
migrates fine, though the first boot's migration takes ~10 min). The pre-upgrade backup (`backup_first`) is skipped
automatically for your token — it would land in admin-only `_backups/`, which
you can't read back — so there's no safety net: if an upgrade breaks the
instance, delete and re-create. The job `result` carries a best-effort
`dhis2_major_version` that may still show the old major until migrations
finish — confirm the upgrade landed by polling `GET /instances?full=1` (or
`/api/system/info`) until it reports the new version.

**Doris analytics backend** (`"analytics": "doris"` on create): adds a
dedicated Apache Doris analytics database as a sidecar container. Only for
tasks that are specifically about the Doris/dedicated-analytics-DB feature —
for normal analytics testing, plain Postgres instances behave identically
to users.

- Requires `version` with DHIS2 major ≥ 42 (`400` otherwise); cannot be
  combined with `war_url`/`war_file`.
- A Doris sidecar needs ~5.5 GB RAM, so your token is capped at
  `D2_BROKER_MAX_AGENT_DORIS` (default **1**) Doris-enabled instances
  (`409` when hit), and creates are slower (Doris image + boot).
- Instance elements have `"analytics": "doris" | null` so you can see which
  instances have it.
- **What actually runs in Doris on v42**: aggregate data-value and
  completeness analytics only. Event, enrollment, and tracked-entity
  analytics tables stay in Postgres until v43 — do not file "missing
  event tables in Doris" as a bug on 42.
- Verify via the API as usual (`POST /api/resourceTables/analytics`, then
  query `/api/analytics`). To inspect Doris directly, it speaks the MySQL
  protocol on dev-net at `dhis2-<name>-doris:9030` (user `root`, empty
  password, database `analytics`): e.g.
  `mysql -h dhis2-<name>-doris -P9030 -uroot -e "SELECT count(*) FROM analytics.analytics"`.
- If the Doris container is recreated while Tomcat keeps running, the next
  analytics run fails with "Communications link failure" (stale JDBC pool)
  — stop/start the instance to fix.

**Memory / heap**: instances default to `-Xmx4g`. Set a different heap at
create with `"memory":"2g"` in the body, or change it later with
`POST /instances/<name>/memory` `{"memory":"2g"}` (recreates Tomcat — a brief
restart; DB preserved). Your token is capped by `D2_BROKER_MAX_AGENT_MEMORY`
(default `8g`); a larger request returns `400`. Size format: `^[0-9]+[mMgG]$`
(e.g. `512m`, `2g`).

**Polling:** poll `GET /jobs/<id>` every 5–10 s. Jobs run on a single global
queue, so `queued` can mean "waiting behind someone else's job" — it is not
a hang. Creates with a `version` download a WAR (hundreds of MB) and seed
restores can take minutes; expect several minutes end-to-end and don't give
up early.

**When a job fails** (`status: "failed"`): the `error` field says which step
exited non-zero; the actual cause is in the output — check `log_tail` in the
job response first, and fetch `GET /jobs/<id>/log` for the full transcript.
Fix and retry rather than asking the user, unless the log shows a host-side
problem (out of disk, Docker down, no free ports).

## Reaching the instance

On success the job `result` includes `devnet_url`, normally
`http://dhis2-agent-mytest:8080`. From inside the sandbox, **always use
`devnet_url`** (both sandbox and instance are on the `dev-net` Docker
network):

```bash
curl -s -u admin:district http://dhis2-agent-mytest:8080/api/system/info
```

- **Never use `localhost_url` or any `localhost:<port>`** — those are the
  ports the instance publishes to the *host*; from inside the sandbox,
  nothing answers on localhost. (`localhost_url` is in the API response for
  the user's benefit.)
- If a DHIS2 instance is *not* broker-created and its name doesn't resolve,
  it probably isn't on dev-net: its host-published port may still be
  reachable on the Docker gateway IP — `ip route | awk '/default/ {print $3}'`,
  then probe `<gateway-ip>:<port>`. Prefer asking the user to attach it to
  dev-net (`d2-dev-net-attach <name>`).

### Direct database access

`result.devnet_db` (normally `dhis2-agent-mytest-db:5432`) is the instance's
PostgreSQL, also on dev-net: database `dhis2`, user `dhis`, password `dhis`.
Use it when API-level checks aren't conclusive — e.g. verifying whether
values were actually stored in `datavalue`, or inspecting `analytics_<year>`
tables when analytics output looks wrong:

```bash
PGPASSWORD=dhis psql -h dhis2-agent-mytest-db -U dhis -d dhis2 \
  -c "SELECT count(*) FROM datavalue;"
```

**If `psql` isn't available** (the standard agent-sandbox ships
`postgresql-client`, but a leaner environment may not), use the pure-Python
`pg8000` driver from PyPI instead — no system package needed:

```bash
pip install pg8000        # pure-Python Postgres driver
```
```python
import pg8000.native
con = pg8000.native.Connection(
    host="dhis2-agent-mytest-db", user="dhis", password="dhis", database="dhis2")
print(con.run("SELECT count(*) FROM datavalue"))
```

### Other notes

- **Default credentials** `admin` / `district` (standard DHIS2 dev default;
  seeds may differ — ask the user if login fails). Some seeds ship with
  `admin` disabled (`401 "Account disabled"`) or with a non-default
  password. DHIS2 caches user details, so a failed login *sticks* until
  Tomcat restarts — verify credentials with `GET /api/me` (Basic auth)
  before building tests on the instance. **Simplest: just use the
  always-present `local_admin` / `district` superuser** (see "Guaranteed
  superuser" above) instead of repairing `admin`. Only if you specifically
  need `admin` itself re-enabled and reset to `district`, edit the DB
  directly (Tomcat won't let you log in to fix it via UI):

  ```bash
  # Generate a bcrypt hash for 'district' (or any chosen password) — DHIS2
  # accepts both $2a$ and $2b$ prefixes at cost 10:
  HASH=$(python3 -c "import bcrypt; print(bcrypt.hashpw(b'district', bcrypt.gensalt(10)).decode())")

  # (if psql is unavailable, run the same UPDATE via pg8000 — see
  #  "Direct database access" below)
  PGPASSWORD=dhis psql -h dhis2-<name>-db -U dhis -d dhis2 \
    -c "UPDATE userinfo SET disabled=false, password='$HASH' WHERE username='admin';"

  # Restart Tomcat to invalidate the cached user details:
  curl -s -X POST -H "$H" $B/instances/agent-<name>/stop
  curl -s -X POST -H "$H" $B/instances/agent-<name>/start
  ```
- **Startup time**: after a create with a version, DHIS2 still needs 1–5
  minutes to boot (Flyway migrations on empty DBs take a while). Poll
  `/api/system/info` until it answers; 502/connection-refused means Tomcat
  is still starting.
- **Warm the app before API-only smoke tests**: on a freshly booted (or
  restarted) instance, some legacy endpoints can persistently 500 —
  e.g. `GET /api/authorities` returning
  `"Struts Dispatcher.getInstance() is null"` on 2.41.9 — until *something*
  has loaded the legacy web layer once. A single GET to a Struts page
  (`/dhis-web-commons/security/login.action`, or the app root in a browser)
  primes it and the 500s go away. Any 500 that only reproduces on a fresh
  boot with curl should be re-verified after this warm-up before it is
  filed as a finding — it's an environment quirk, not an app bug.
- An empty instance (created with a `version` but no seed) has no
  organisation units or metadata, and only the `admin` and `local_admin`
  users — import what you need, or use a seed.
- For browser/Playwright login use **Basic-auth `GET /api/me`** to get a
  session cookie; the React login page resists programmatic form fills.
  Do **not** use `POST /api/auth/login` — it doesn't exist on 2.40 and
  responds with a 302 to the legacy login page; a client that follows
  redirects silently captures an anonymous cookie (HTTP 200, no error) and
  your test then renders the login screen instead of the app.

## Etiquette

- Test runs **mutate** the instance (imported data, changed settings,
  metadata edits). For reproducible results, reset from a seed — or delete
  and re-create — between runs rather than reusing dirty state.
- Reuse your existing `agent-*` instance when it fits; reset instead of
  recreate when you just need clean data.
- Delete instances when a task is done, unless the user wants them kept.
- Creates and restores are heavy (minutes); don't fire-and-forget several
  speculatively. Tell the user what you're creating and why.
