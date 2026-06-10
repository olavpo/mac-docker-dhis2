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
- There is a cap on concurrent `agent-*` instances. Delete instances you no
  longer need.

## Operations

All mutating calls return **202 with a job**; poll until terminal status
(`succeeded` / `failed`). Operations are queued globally, one at a time.

```bash
B="$DHIS2_BROKER_URL"; H="Authorization: Bearer $DHIS2_BROKER_TOKEN"

# What exists / what seeds are available
curl -s -H "$H" $B/instances
curl -s -H "$H" $B/seeds

# Create (version optional — omit both version and seed for an empty shell
# with no DHIS2 deployed; give "version" to deploy that DHIS2 release)
curl -s -X POST -H "$H" -d '{"name":"agent-mytest","version":"2.42.4"}' $B/instances
curl -s -X POST -H "$H" -d '{"name":"agent-mytest","version":"42","seed":"<path from /seeds>"}' $B/instances

# Poll the job (id from the 202 response)
curl -s -H "$H" $B/jobs/<job-id>          # status + log_tail + result
curl -s -H "$H" $B/jobs/<job-id>/log      # full log, plain text

# Reset the database to a known seed state (Tomcat restarts automatically)
curl -s -X POST -H "$H" -d '{"seed":"<path from /seeds>"}' $B/instances/agent-mytest/reset

# Start / stop / delete
curl -s -X POST -H "$H" -d '{}' $B/instances/agent-mytest/start
curl -s -X POST -H "$H" -d '{}' $B/instances/agent-mytest/stop
curl -s -X DELETE -H "$H" $B/instances/agent-mytest
```

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

### Other notes

- **Default credentials** `admin` / `district` (standard DHIS2 dev default;
  seeds may differ — ask the user if login fails).
- **Startup time**: after a create with a version, DHIS2 still needs 1–5
  minutes to boot (Flyway migrations on empty DBs take a while). Poll
  `/api/system/info` until it answers; 502/connection-refused means Tomcat
  is still starting.
- An empty instance (created with a `version` but no seed) has no
  organisation units, users besides admin, or metadata — import what you
  need, or use a seed.
- For browser/Playwright login use `POST /api/auth/login` with JSON body to
  get a session cookie; the React login page resists programmatic form fills.

## Etiquette

- Test runs **mutate** the instance (imported data, changed settings,
  metadata edits). For reproducible results, reset from a seed — or delete
  and re-create — between runs rather than reusing dirty state.
- Reuse your existing `agent-*` instance when it fits; reset instead of
  recreate when you just need clean data.
- Delete instances when a task is done, unless the user wants them kept.
- Creates and restores are heavy (minutes); don't fire-and-forget several
  speculatively. Tell the user what you're creating and why.
