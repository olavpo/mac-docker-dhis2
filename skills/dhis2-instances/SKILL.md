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
- **An `agent-*` instance you didn't create this session may belong to a
  concurrently running session** — it is not automatically stale. Prefer
  creating your own instance over reusing or deleting someone else's; pass a
  `label` on create (e.g. `{"label": "review of tool-x"}`) so other sessions
  can recognise yours, and check `created_at`/`label` in `GET /instances`
  before treating a leftover as abandoned.
- Seeds: **only the curated list from `GET /seeds`** (demo/test databases the
  user has approved for agent use), or **no seed at all** for a clean, empty
  DHIS2 that initializes itself on first start. You cannot restore real
  backups, host files, or URLs — don't ask the broker to; it will refuse.
  Each seed element carries a `dhis2_version` hint (from its filename) — use
  it to plan version matrices — and most carry **`notes`**: what the seed
  is, how current its data is (`data_until`), whether its own `admin` works
  (`admin_user`), whether analytics works (`analytics_ok`) and
  `known_issues`. **Read the notes before you pick a seed**: they say which
  seeds are metadata-only (useless for analytics or performance tests),
  which ship duplicate or dangling metadata, and which cache analytics
  answers. A seed one major older than the instance boots fine, but Flyway
  migrates it on first boot (10–25 minutes with nothing on `/api`), so pick
  the seed whose version matches.
  **Seeds are snapshots and age at different rates.** Relative periods
  (`LAST_12_MONTHS` etc.) silently return nothing on a stale seed: before
  building tests on them, check data recency (e.g.
  `SELECT max(occurreddate) FROM event;` or the newest period in a quick
  analytics query) and prefer fixed periods that match the data.
- There is a cap on concurrent `agent-*` instances — it counts **stopped
  instances too**, including stale leftovers from earlier sessions. When the
  cap is hit, the error lists every candidate with its status, stopped ones
  first; delete only `agent-*` ones (and see the ownership caveat above —
  prefer the oldest `created_at`, stopped, unlabelled candidates). A create
  that **fails** is deleted by the broker, so failed attempts don't use up
  slots. In practice, keep the live footprint to
  **1 running instance where possible, 2 max**: two DHIS2 instances *booting*
  concurrently starve each other (20+ minutes with nothing on `/api`). The
  broker now guards against this — a create/reset job stays `running` until its
  instance's API is up, so a second create you submit waits its turn rather
  than booting alongside the first — but keep the footprint low anyway for RAM.
  The same contention applies to **any heavy DB work, not just boots**: a seed
  restore on one instance while another ran analytics stretched an ~11-minute
  analytics run to ~25 minutes. The broker serializes its own jobs, but work
  you trigger through an instance's API (analytics runs, large imports/deletes)
  is invisible to it — so as a rule, run **one heavy operation across all
  instances at a time**. Large *synchronous* metadata imports or deletes
  count: a 16,000-object `importStrategy=DELETE` that hadn't answered after
  300 s took the shared Tomcat down with it. When several agents share one
  instance, give one of them ownership of restarts and analytics runs.
- **Instances can be found `stopped` mid-session** (host-side resource
  management or the user intervening). A long-running dev server pointed at
  one then fails with confusing DNS/proxy errors, not a clear "upstream
  gone". If something that worked stops resolving, check `GET /instances`
  first — `start` is cheap and preserves state.
- **`running` does not mean reachable moment-to-moment**: a Tomcat restart
  (often the JVM running out of memory under analytics load) leaves the
  status `running` while the API refuses connections for a minute or two. If
  a test suddenly fails with connection-refused or a timeout, check
  `GET /instances?full=1`: `containers.tomcat.restart_count`,
  `oom_killed` and `started_at` show whether Tomcat restarted and when.
  A restart erases DHIS2's in-memory job notifications, so a running
  analytics job then shows `jobStatus: RUNNING` forever with an empty task
  list; it is dead, not slow. Re-run once the API answers, before treating
  the failure as an app bug.
- **Backups are admin-only**: `POST /instances/<name>/backup` returns `403`
  for your token (`_backups/` is the user's territory). To get clean or known
  state, reset from a seed or delete and re-create — don't try to back up.
  If the user wants a dump of your instance to share or restore elsewhere,
  see "Sharing an instance as a dump" below.
- **Guaranteed superuser:** every instance you create or reset has a
  `local_admin` / `district` superuser (`ALL` authority), added by the host
  tooling regardless of the restored database's own `admin`. Make it your
  default login: a seed's `admin` may be disabled, have an unknown password,
  or — subtler — authenticate fine yet **lack the `ALL` authority**, so
  anything that creates users or grants roles fails with
  `409 ... is not allowed to grant users access to user role ...`. It's
  stripped from backups, so it never appears in dumps.
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

**Response shapes differ:** the 202 wraps the job as
`{"job": {...}, "poll": "/jobs/<id>", "log": "/jobs/<id>/log"}`, but
`GET /jobs/<id>` returns the **bare job object** (same fields, no `"job"`
wrapper, plus `log_tail`). Don't write polling code that expects the
wrapper on both; `d = r.get("job", r)` reads either shape. A `KeyError`
inside a `$(...)` poll loop fails silently and ends the loop early, so a
running job then looks finished.

```bash
B="$DHIS2_BROKER_URL"
H="Authorization: Bearer $DHIS2_BROKER_TOKEN"
CT="Content-Type: application/json"

# Identify yourself on every mutating call: the broker logs it with the job,
# so the user can tell which session created, reset or deleted what.
ME="X-D2-Client: $(hostname)"

# What exists / what seeds are available (read the seeds' "notes")
curl -s -H "$H" $B/instances
curl -s -H "$H" $B/seeds

# Create (always pass a label so other sessions can recognise your instances)
curl -s -X POST -H "$H" -H "$ME" -H "$CT" -d '{"name":"agent-mytest","version":"42","label":"<what this is for>"}' $B/instances
curl -s -X POST -H "$H" -H "$ME" -H "$CT" -d '{"name":"agent-mytest","version":"2.42.4","seed":"<path from /seeds>","label":"<what this is for>"}' $B/instances

# Readiness (full=1 adds "readiness": deploying | migrating | ready | null)
curl -s -H "$H" "$B/instances?full=1"

# Poll the job (id from the 202 response)
curl -s -H "$H" $B/jobs/<job-id>          # status + log_tail + result
curl -s -H "$H" $B/jobs/<job-id>/log      # full log, plain text

# Reset the database to a known seed state (Tomcat restarts automatically)
curl -s -X POST -H "$H" -H "$ME" -H "$CT" -d '{"seed":"<path from /seeds>"}' $B/instances/agent-mytest/reset

# Start / stop / delete
curl -s -X POST -H "$H" -H "$ME" $B/instances/agent-mytest/start
curl -s -X POST -H "$H" -H "$ME" $B/instances/agent-mytest/stop
curl -s -X DELETE -H "$H" -H "$ME" $B/instances/agent-mytest

# Upgrade in place — swap the WAR, keep the database (see note below)
curl -s -X POST -H "$H" -H "$ME" -H "$CT" -d '{"version":"2.42"}' $B/instances/agent-mytest/upgrade

# Change the Tomcat heap (recreates Tomcat; DB preserved)
curl -s -X POST -H "$H" -H "$ME" -H "$CT" -d '{"memory":"2g"}' $B/instances/agent-mytest/memory

# Generate analytics tables and wait for completion (see "Running analytics")
curl -s -X POST -H "$H" -H "$ME" -H "$CT" -d '{"last_years":2}' $B/instances/agent-mytest/analytics

# Restart only Postgres (seconds), e.g. after ALTER SYSTEM
curl -s -X POST -H "$H" -H "$ME" $B/instances/agent-mytest/restart-db
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

**Blank instances are cheap** — about 2 minutes for a blank 2.42/2.43 and
2.5–3 minutes for an SL-seeded 2.40/2.41, so version matrices are practical.
Two per-version steps before the first real work on a blank instance:
- **≤ 2.42:** the first metadata import after first boot can fail with a
  `PropertyValueException` on `DataSet.periodType` even though the payload
  sets it. Run `POST /api/maintenance/cacheClear` once before importing
  (no stop/start needed on 2.42.6; 2.43 is unaffected).
- **2.43:** set the data output period types,
  `POST /api/configuration/dataOutputPeriodTypes`, or analytics returns
  nothing (DHIS2-20379).

Name a throwaway instance what the project's scripts already expect (e.g.
`agent-subexpr` for `BASE=http://dhis2-agent-subexpr:8080`) and they run
unmodified.

**Time zone** (`"timezone": "Africa/Lagos"` on create; default `Etc/UTC`):
sets the server time zone for Tomcat, the JVM and Postgres. Set it to the
deployment's zone whenever dates matter, above all for Android or other
device testing: DHIS2 rejects enrollment and event dates it thinks are in the
future using the *server's* date, so a UTC server and a UTC+1 device break
every sync between local midnight and UTC midnight. Before a device session,
compare `adb shell date` with `serverDate` in `/api/system/info`.

**Dev-server CORS** is set for you: after every create and reset the broker
adds `http://localhost:3000` and `http://localhost:8080` to the instance's
CORS allowlist (the `[cors]` line in the job log). If your dev server runs on
other ports, pass them on create: `"cors_origins": ["http://localhost:4000"]`.

**Upgrading an instance** (`POST /instances/<name>/upgrade`): swaps the WAR
while keeping the database and volumes, then lets Flyway migrate on boot.
**Only reach for this when the task is specifically about an upgrade/migration
path** (e.g. "does this metadata survive a 2.41 → 2.42 upgrade"). If you just
need an instance at version X, create one at X — don't create-then-upgrade.
For your token: only the `version` form works (`war_url`/`war_file` are
admin-only → 403), and only same-major or one-major-up is allowed (downgrades
and skips → 400). **2.41 → 2.42 works too**: instances created at ≤ 2.41 run
Tomcat 9 and 2.42+ needs Tomcat 10, so the broker swaps the servlet container
as part of the upgrade (same ports, heap and time zone; the database volume
is kept) before deploying the new WAR. This is the way to carry a database
you built yourself across that boundary — no manual dump/restore/rename. The pre-upgrade backup (`backup_first`) is skipped
automatically for your token — it would land in admin-only `_backups/`, which
you can't read back — so there's no safety net: if an upgrade breaks the
instance, delete and re-create. The upgrade job holds until the instance is
ready before finishing, so its `result.dhis2_major_version` reflects the
migrated schema — no separate poll needed to confirm the upgrade landed.
Timing varies a lot: 2.41 → 2.42 on an empty database took 1.5 minutes, but
2.42 → 2.43 on a tiny one took ~28 minutes (WAR download, unpacking,
Flyway) where a fresh create at 2.43 took ~6. Prefer create unless the
upgrade path is the thing under test.

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

**One job per instance:** submitting a job while the instance already has
one queued/running returns `409` (`already has an active job`) — wait for
the active job first. The exception is **delete**, which you may submit at
any time: it queues behind the active job and runs after it (handy when a
start/create is holding for minutes and you've decided you don't need the
instance). **A queued delete fires later, whatever you are doing then**: a
delete submitted by an earlier script ran in the middle of a later manual
test on the same instance. Before reusing an instance, check `GET /jobs` for
pending jobs on it.

**When a job fails** (`status: "failed"`): the `error` field says which step
exited non-zero; the actual cause is in the output — check `log_tail` in the
job response first, and fetch `GET /jobs/<id>/log` for the full transcript.
Fix and retry rather than asking the user, unless the log shows a host-side
problem (out of disk, Docker down, no free ports). A failed create has
already been deleted by the broker, so retry with the same name.

**`interrupted`** means the broker restarted. The job's `error` says what to
do: a job that was still queued never ran, so submit it again; a create or
reset that was running left the instance half-done, so delete it (or reset
it) and retry.

## Reaching the instance

On success the job `result` includes `devnet_url`, normally
`http://dhis2-agent-mytest:8080`. From inside the sandbox, **always use
`devnet_url`** (both sandbox and instance are on the `dev-net` Docker
network):

```bash
curl -s -u local_admin:district http://dhis2-agent-mytest:8080/api/system/info
```

- **The host port is not stable**: a re-created instance can come back on a
  different `http_port`. Only the dev-net name is a stable handle, so never
  hard-code a host port in setup docs or scripts.
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

**Do database surgery on a running instance.** `stop` removes the database
container from dev-net too, so `dhis2-<name>-db` stops resolving. To swap a
database underneath a running DHIS2, block reconnects first —
`ALTER DATABASE dhis2 ALLOW_CONNECTIONS false;` plus `pg_terminate_backend`
— because Tomcat's pool reconnects instantly; then rename, re-allow, and
stop/start the instance.

### Sharing an instance as a dump

`/backup` is admin-only, so when the user wants a dump of your instance (for
another server, a colleague, a bug report), take it yourself — and **disable
`local_admin` in it**: the broker's superuser (`local_admin`/`district`, ALL
authority) is in every instance's database and must not travel.

```bash
PGPASSWORD=dhis pg_dump -h dhis2-<name>-db -U dhis -d dhis2 \
  --no-owner --no-privileges -T 'analytics*' > dump.sql
echo "UPDATE userinfo SET disabled = true WHERE username = 'local_admin';" >> dump.sql
gzip dump.sql
```

`-T 'analytics*'` leaves out the analytics tables (usually most of the size;
the receiver re-runs analytics). To check the dump, restore it into a scratch
database on the same server (`dhis` may create databases) and compare row
counts.

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
- **After a host restart or unclean stop, analytics can be empty while
  `datavalue` is intact.** Since 2.41 analytics tables are UNLOGGED, and
  Postgres crash recovery truncates them. Re-run analytics before diagnosing
  data loss.
- **Analytics answers can be cached.** Some seeds set
  `keyCacheStrategy` (e.g. `CACHE_1_MINUTE`), and metadata changes don't
  evict cached `/api/analytics` responses, so a before/after comparison
  reads the old answer. Set it off first:
  `curl -X POST -u local_admin:district "<url>/api/systemSettings/keyCacheStrategy?value=NO_CACHE"`.
- **On a freshly seeded instance, don't trust an empty list.** On a 2.41
  seed, `/api/organisationUnitGroups` and similar legacy list endpoints
  returned `total: 0` until Tomcat restarted, while `/gist` and by-UID reads
  were right. Likewise `/api/apps` and `/api/apps/menu` fill in for some
  minutes after the API answers: a short list then is not proof an app is
  missing — open `<url>/apps/<key>` to check. Re-check via `/gist` or after a
  stop/start before reporting metadata or apps as absent.
- **Startup time**: DHIS2 needs 1–5 minutes to boot after its containers are
  up (10–25 on a cross-version seed migration). The broker now folds this wait
  into the job itself — a create/reset job stays `running` until the instance's
  API answers, then goes terminal — so **a `succeeded` create/reset job already
  means the API is up and `local_admin` logs in**; you usually don't need a
  separate readiness poll.
  `GET /instances?full=1` still reports `readiness`
  (`deploying`/`migrating`/`ready`) if you want to watch a boot in progress.
- **Probe directly to start work as early as possible**: for **metadata** work
  (imports, indicators, schemas, API behavior) the API is usable well before a
  boot fully settles — probe it yourself rather than waiting on the job or
  `readiness: ready`. An authenticated `curl -u local_admin:district
  <url>/api/system/info` returning a `version`, or any real metadata query
  returning `200`, means you can go; 401/403 also means the API is up (auth is
  enforcing); 404/502/connection-refused mean Tomcat is still starting.
  **Caveat: API-usable ≠ analytics/resource tables built.** `readiness: ready`
  (and a direct `system/info`) reflect metadata usability only — do **not**
  kick off analytics on that signal; an analytics run needs its resource tables
  generated first.
- **Running analytics to completion** (needed before any `/api/analytics`
  query returns data; broker-created instances have no analytics tables at
  all until the first run). Use the broker job:

  ```bash
  curl -s -X POST -H "$H" -H "$ME" -H "$CT" -d '{"last_years":2}' $B/instances/agent-<name>/analytics
  ```

  It starts the run, follows DHIS2's task notifications into the job log,
  and ends `succeeded` only when DHIS2 reports completion without error. It
  fails, instead of hanging, if Tomcat dies mid-run or a stale `RUNNING`
  analytics job blocks the new one (the error says how to clear it). It
  runs on the broker's single queue, so it never overlaps another
  instance's restore or analytics run — and other sessions' creates wait
  behind it. `last_years` counts back from *today*: on a seed whose data
  ends in 2023, `last_years: 3` in 2026 builds nothing useful; check the
  seed's `data_until` note. Omit it to build every year.
  Expect **~5–15 min on the SL seeds** (v43 on 2.43.1: ~9 min for the first
  run, `last_years: 1` ~2.5 min), longer under contention. Two traps when
  you check results yourself: a run interrupted in the index phase can still
  post "Analytics tables updated" with only some indexes built (compare
  `SELECT tablename, count(*) FROM pg_indexes WHERE tablename LIKE 'analytics_20%' GROUP BY 1`
  across years), and analytics *responses* are cached, so a query repeated
  after a run can return the old answer until
  `POST /api/maintenance/cacheClear` (`/api/maintenance/analyticsCacheClear`
  is 404). On 2.43.1, cancel during the index phase is ignored: the run
  finishes but is recorded `STOPPED`. If the task never touches aggregate
  data (tracker-only work), `TRUNCATE datavalue` via direct DB access
  before generating analytics — it cut runtime from 30+ min to ~5 min on
  the big seeds (`skipTableTypes` is not honored on 2.41).
- **Warm the app before API-only smoke tests** (the broker now does this
  after every create, reset, start, upgrade and memory change — the
  `[warm-up]` line in the job log — so this matters mainly after a Tomcat
  restart you didn't go through the broker for): on a freshly booted (or
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
  users — import what you need, or use a seed. After importing an org-unit
  hierarchy, users still have **no org units assigned**, so apps render
  empty states (`/api/me` returns no `organisationUnits`): assign the
  hierarchy root to `organisationUnits`, `dataViewOrganisationUnits` and
  `teiSearchOrganisationUnits` on the users you test with. On 2.42 a
  plain-JSON `PATCH /api/users/<id>` is rejected — use the JSON-Patch
  content type (`application/json-patch+json`).
- **CORS**: the broker allowlists `http://localhost:3000` and
  `http://localhost:8080` (plus any `cors_origins`) after create and reset.
  For other origins, `POST /api/configuration/corsAllowlist` with a bare JSON
  array of *all* wanted origins (it replaces the list; returns 204). It is a
  configuration resource, not a system setting — there is no
  `keyCorsWhitelist` under `/api/systemSettings`. A login from a
  non-allowlisted origin returns an empty HTTP 200 with no session cookie,
  not an error.
- **Exercising job/scheduler features**: no seed ships a running job or a
  scheduler queue. Build one in seconds: create 2–3 job configurations
  (`POST /api/jobConfigurations` with e.g.
  `{"name": "t1", "jobType": "ANALYTICS_TABLE", "cronExpression": "0 0 3 * * ?"}`),
  then group them into a queue with
  `POST /api/scheduler/queues/<name>` (`{"cronExpression": …, "sequence": ["<uid1>", "<uid2>"]}`).
  For a job that stays `RUNNING` long enough to watch, use the built-in
  **`TEST` job type** (present on 2.40–2.43; analytics on an empty instance
  finishes before a UI poll and its once-off configuration vanishes within a
  minute):
  `POST /api/jobConfigurations {"name":"t","jobType":"TEST","cronExpression":"0 0 5 1 1 ?","jobParameters":{"stages":3,"items":20,"itemDuration":1500}}`,
  then `POST /api/jobConfigurations/<id>/execute` gives ~90 s of `RUNNING`
  (executing it again meanwhile → 409). Other parameters: `waitMillis`,
  `failAtStage`, `failAtItem`, `failWithMessage`, `failWithException`,
  `failWithPolicy`, `runStagesParallel`. Facts that bite:
  - `TEST` writes **no** notifications to `/api/system/tasks`. For visible
    progress use an async metadata import instead
    (`POST /api/metadata?async=true` with a few thousand generated data
    elements emits `Creating N DataElement object(s)` lines).
  - `executedBy` is set only on once-off jobs (like that async import);
    `/execute` on a scheduled configuration leaves it null, so
    "executed by this user" permission paths can't be tested via `execute`.
  - **2.40 has no `POST /api/jobConfigurations/<id>/cancel`** (from 2.41).
    On 2.40 the path falls into a generic handler and answers 500 or 404,
    so a non-404 from a bogus-uid probe does not prove the endpoint exists.
  - Cancel on 2.41–2.43 returns 204; a `TEST` job stops at the next stage
    boundary (~30 s) with `lastExecutedStatus: STOPPED`. A user with neither
    `F_PERFORM_MAINTENANCE` nor a matching `executedBy` gets 403.
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
