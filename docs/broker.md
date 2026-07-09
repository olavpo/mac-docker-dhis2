# d2-broker — HTTP API for DHIS2 instance management

`d2-broker` is a small HTTP service that wraps the `d2-*` scripts so other
clients can create, reset, start/stop, back up, upgrade and delete DHIS2
Docker instances on this machine without shell access. Current and intended
clients:

- **AI agent sandboxes** ([ai-agentic-sandbox](https://github.com/olavpo/ai-agentic-sandbox))
  reach it at `http://host.docker.internal:9300` with a restricted token.
- **Your own tooling** (a menu bar widget, a dashboard, `curl` from the host)
  with the admin token.

It is a single Python 3 file with no dependencies:
`bash-scripts-docker/d2-broker` (symlinked into `/usr/local/bin` by
`setup.sh` like the other tools).

## Quick start

```bash
d2-broker run        # foreground, for trying it out
d2-broker install    # launchd agent: starts at login, restarts on crash
d2-broker tokens     # print the two API tokens
d2-broker status     # launchd + health check
d2-broker uninstall
```

Configuration is via env vars (set before `install`, they are baked into the
launchd plist):

| Variable | Default | Meaning |
|---|---|---|
| `DHIS2_BASE` | (required) | Same base directory as the other d2-* tools |
| `D2_BROKER_PORT` | `9300` | Listen port |
| `D2_BROKER_BIND` | `127.0.0.1` | Bind address — loopback-only by default; see below |
| `D2_BROKER_MAX_AGENT_INSTANCES` | `5` | Cap on concurrent `agent-*` instances |
| `D2_BROKER_JOB_TIMEOUT` | `7200` | Per-job timeout, seconds |
| `D2_DEFAULT_MEMORY` | `4g` | Default Tomcat max heap for new instances (`d2-instance-create`) |
| `D2_BROKER_MAX_AGENT_MEMORY` | `8g` | Max heap an agent token may request |

State lives in `$DHIS2_BASE/_broker/`: `tokens.json` (mode 0600),
`broker.log`, `jobs/<id>.json` + `jobs/<id>.log`, `downloads/` (temporary
seed downloads).

## Security model

**Loopback-only by default.** The broker binds `127.0.0.1`, so nothing on
the LAN can reach it. Containers still can: on Docker Desktop for Mac,
`host.docker.internal` traffic is relayed by the Docker Desktop process on
the host, which connects to loopback (verified). On Linux with
`host-gateway`, that relay doesn't exist — set `D2_BROKER_BIND` to the
docker bridge IP (or `0.0.0.0`) there.

**Two bearer tokens** (`$DHIS2_BASE/_broker/tokens.json`), both mandatory —
there is **no unauthenticated localhost bypass**, because that same Docker
Desktop relay makes container traffic arrive *from* 127.0.0.1, so source-IP
trust cannot distinguish the sandbox from your own curl:

- **admin** (`scope: all`) — full access. For the user's own clients.
- **agent** (`scope: agent`) — for AI agents. Restricted to:
  - instances named `agent-*` only (create/reset/start/stop/upgrade/delete/
    list); it can never touch your own instances;
  - seeds from the **curated `$DHIS2_BASE/_seeds/` directory only** — agents
    can never restore real-data backups (`_backups/` is admin-only), pass
    arbitrary host paths, or download from URLs;
  - no custom WARs (`war_url` / `war_file` are admin-only); versions come
    from releases.dhis2.org via `d2-deploy-war -v`. Upgrades are limited to
    the `version` form for the same reason;
  - **no backups** — `POST /instances/<name>/backup` is admin-only (`_backups/`
    is admin territory); agents get a clean slate via seeds/reset instead, and
    an agent `upgrade` silently skips its `backup_first` step for the same
    reason;
  - may set the Tomcat heap (`memory` on create, `POST .../memory`), but only
    up to `D2_BROKER_MAX_AGENT_MEMORY` (default `8g`);
  - at most `D2_BROKER_MAX_AGENT_INSTANCES` instances at a time.

**Fixed verb set.** The broker only ever executes the sibling `d2-*` scripts
with validated arguments (instance names `^[a-z][a-z0-9_-]{1,29}$`, versions
`^[0-9][0-9.]*$`, seeds resolved against their allowed directory with
traversal checks). There is no endpoint that runs arbitrary commands.

**Curated seeds.** Put demo/test dumps you are happy for agents to use in
`$DHIS2_BASE/_seeds/` (e.g. a small demo DB, an empty-but-configured
baseline). Everything in `$DHIS2_BASE/_backups/` stays invisible to agent
tokens. For a fully clean instance, agents create without a seed — DHIS2
initializes an empty database via Flyway on first start.

## Jobs

Create, reset, start, stop, delete, backup and upgrade all return **202 with
a job**, because the underlying operations range from seconds (stop) to hours
(large restore).
Poll `GET /jobs/<id>` until `status` is terminal. Jobs run on a **single
worker, globally serialized** — deliberate, since `d2-instance-create`
auto-selects free ports by scanning, so concurrent creates could collide.

Job statuses: `queued → running → succeeded | failed`; `interrupted` marks
jobs that were in flight when the broker was restarted.

## API

All requests: `Authorization: Bearer <token>`. All responses JSON except
`/jobs/<id>/log` (plain text). Request bodies are parsed as JSON regardless
of the Content-Type header. `version` accepts a major (`42`, `2.42` → latest
stable resolved from releases.dhis2.org) or an exact release (`2.42.4`).

| Method & path | Description |
|---|---|
| `GET /health` | Liveness (no auth) |
| `GET /instances[?full=1]` | List instances (agent scope: `agent-*` only). `full=1` adds `dhis2_major_version` (slower) |
| `POST /instances` | Create. Body: `{"name", "version"?, "seed"?, "tomcat"?, "memory"?, "http_port"?, "pg_port"?, "war_url"?†, "war_file"?†}` → 202 job |
| `POST /instances/<name>/reset` | Restore DB from a seed. Body: `{"seed"}` → 202 job |
| `POST /instances/<name>/start` | `docker compose up -d` → 202 job |
| `POST /instances/<name>/stop` | `docker compose down` → 202 job |
| `DELETE /instances/<name>` | Stop, remove containers+volumes, delete dir → 202 job |
| `POST /instances/<name>/backup`†| `pg_dump` the DB to `_backups/<name>/`. Body: `{"label"?}` → 202 job. Requires a running DB. `result` is a `GET /seeds` entry |
| `POST /instances/<name>/upgrade` | Swap the WAR, preserving DB+volumes. Body: `{"version" \| "war_url"† \| "war_file"†, "tomcat"?, "backup_first"?}` → 202 job. Rejects downgrades, major-skips, and Tomcat changes |
| `POST /instances/<name>/memory` | Set Tomcat max heap (`-Xmx`) + recreate Tomcat. Body: `{"memory"}` → 202 job |
| `GET /seeds` | Seeds available to this token (agent: `_seeds/`; admin: also `backups/...`) |
| `GET /jobs` | Recent jobs (agent scope: `agent-*` jobs only) |
| `GET /jobs/<id>` | Job status incl. `log_tail` and, on success, `result` (instance info) |
| `GET /jobs/<id>/log` | Full job log, plain text |

† admin token only.

`tomcat` is optional and auto-selected from `version` (DHIS2 ≤ 2.41 → Tomcat 9,
≥ 2.42 → Tomcat 10); a `tomcat` that conflicts with `version` fails the job.

`http_port`/`pg_port` are optional (integers 1024–65535); omitted, the broker
auto-selects a port not used or reserved by any other instance (running or
stopped), so new instances no longer collide with stopped ones.

`seed` accepts, by scope:

| Form | Example | Who |
|---|---|---|
| Relative path in `_seeds/` | `"sl-demo-v42.sql.gz"` | agent + admin |
| `backups/` path in `_backups/` | `"backups/acdc/acdc_2026-03-03.sql.gz"` | admin |
| Absolute path | `"/tmp/dump.sql.gz"` | admin |
| URL (downloaded in the job) | `"https://databases.dhis2.org/.../dhis2-db-sierra-leone.sql.gz"` | admin |

Seed files must end in `.sql`, `.sql.gz` or `.pgc` (this is how
`d2-db-restore` picks its restore method).

### Examples

```bash
TOKEN=...   # from: d2-broker tokens
B=http://localhost:9300

# Create an empty 2.42 instance and wait for it
job=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" \
      -d '{"name":"agent-test1","version":"2.42.4"}' $B/instances \
      | python3 -c 'import json,sys;print(json.load(sys.stdin)["job"]["id"])')
watch curl -s -H "Authorization: Bearer $TOKEN" $B/jobs/$job

# Reset it from a curated seed
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
     -d '{"seed":"sl-demo-v42.sql.gz"}' $B/instances/agent-test1/reset

# Tear it down
curl -s -X DELETE -H "Authorization: Bearer $TOKEN" $B/instances/agent-test1
```

On success, a create/reset/start/upgrade job's `result` carries the instance
info, including `devnet_url` (`http://dhis2-<name>:8080`, reachable from
containers on the `dev-net` Docker network), `devnet_db` (`dhis2-<name>-db:5432`,
PostgreSQL `dhis`/`dhis`/`dhis2` — the broker attaches the DB container of
created instances to dev-net as a debugging side-door), and `localhost_url`
(host browser; not reachable from inside containers). An `upgrade` result adds
a best-effort `dhis2_major_version` (it may still read the pre-upgrade major
until Flyway finishes migrating — re-poll `GET /instances?full=1`). A `backup`
job's `result` is instead the new backup's `GET /seeds` entry.

## Sandbox integration (ai-agentic-sandbox)

`agent-sandbox start` reads the **agent** token from
`$DHIS2_BASE/_broker/tokens.json` and passes `DHIS2_BROKER_URL` +
`DHIS2_BROKER_TOKEN` into the container; the sandbox's egress firewall opens
`host.docker.internal` on the broker port only. The Claude Code skill in
`skills/dhis2-instances/` (symlinked into `~/.claude/skills/` by
`d2-broker install`) teaches agents the API.

## Design notes

- **Why HTTP and not a file queue or docker.sock?** A fixed-verb HTTP API is
  client-agnostic (sandbox, widget, curl), needs no watcher process, and
  never hands out host-shell- or docker-equivalent access.
- **Why global job serialization?** Port auto-selection in
  `d2-instance-create` races under concurrency; instance ops are heavy
  anyway.
- **Restarts:** in-flight jobs are marked `interrupted`; logs and job
  snapshots persist on disk. The underlying d2-* script may have partially
  completed — check the job log.
