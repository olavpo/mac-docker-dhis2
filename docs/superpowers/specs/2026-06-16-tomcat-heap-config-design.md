# Configurable Tomcat heap for DHIS2 instances — design

Lets users set the Java max heap (`-Xmx`) for a DHIS2 instance's Tomcat, both
when creating an instance and on an existing one, via the CLI and the
`d2-broker` HTTP API.

Today heap is governed by `CATALINA_OPTS` in the compose templates
(`_templates/docker-compose-tomcat{9,10}.yml`), which set no `-Xmx` — so the
JVM falls back to its container default (~25% of available RAM). This feature
makes the heap explicit and adjustable.

## Decisions (resolved during brainstorming)

- **Knob:** max heap `-Xmx` only. The JVM chooses `-Xms`. No `-Xms` control,
  no container `mem_limit`.
- **Default:** new instances default to `-Xmx4g`, overridable per-host via the
  `D2_DEFAULT_MEMORY` env var. A size is always explicit — there is no
  "no `-Xmx` / JVM default" option once this ships.
- **Scope:** set at create *and* change on an existing instance.
- **Surfaces:** CLI (`d2-instance-create -m`, new `d2-set-memory`) and the
  broker (create `memory` field, new `POST /instances/<name>/memory`).
- **Agent scope:** agents may set/raise heap, capped by
  `D2_BROKER_MAX_AGENT_MEMORY` (default `8g`, above the 4g instance default so
  agents can increase but not exhaust the host). Admin is uncapped.
- **Value format:** `^[0-9]+[mMgG]$` (e.g. `512m`, `2g`, `4096m`).

## Mechanism

`-Xmx<size>` is appended to `CATALINA_OPTS`. At create time the compose
template carries a `${JAVA_MEM_OPTS}` placeholder that `d2-instance-create`
fills. For an existing instance, `d2-set-memory` rewrites the `CATALINA_OPTS`
line in that instance's `docker-compose.yml` and recreates the tomcat
container (DB and volumes are untouched).

## 1. Compose templates

Both `_templates/docker-compose-tomcat9.yml` and
`_templates/docker-compose-tomcat10.yml` change the `CATALINA_OPTS` line to:

```yaml
      CATALINA_OPTS: "-Ddhis2.home=/DHIS2_home -Dlog4j2.configurationFile=/DHIS2_home/log4j2.xml${JAVA_MEM_OPTS}"
```

`d2-instance-create` substitutes `${JAVA_MEM_OPTS}` (via the same `sed` step
that fills `PG_PORT`/`HTTP_PORT`/`INSTANCE_NAME`) with ` -Xmx<size>` (leading
space). Because the default size is always set, the produced
`docker-compose.yml` always contains an `-Xmx`.

## 2. CLI — create (`d2-instance-create`)

- New default near the other defaults: `DEFAULT_MEMORY="${D2_DEFAULT_MEMORY:-4g}"`.
- New `-m <size>` option (`getopts "v:l:s:p:g:f:t:m:"`). After parsing,
  `MEMORY="${MEMORY:-$DEFAULT_MEMORY}"`.
- Validate `MEMORY` against `^[0-9]+[mMgG]$`; exit non-zero with a clear
  message on mismatch.
- Build `JAVA_MEM_OPTS=" -Xmx${MEMORY}"` and add `s/\${JAVA_MEM_OPTS}/$JAVA_MEM_OPTS/g`
  to the existing `sed` substitution.
- Update `usage()` (`-m <size>   Java max heap, e.g. 2g (default: 4g)`) and the
  final summary (`  Tomcat heap:  -Xmx<size>`).

## 3. CLI — change existing (new `d2-set-memory`)

`d2-set-memory <instance name> <size>`:

- Require `DHIS2_BASE`; require exactly two args (else usage).
- Require the instance directory to exist.
- Validate `<size>` against `^[0-9]+[mMgG]$`.
- Require `$DHIS2_BASE/<instance>/docker-compose.yml` to exist.
- Rewrite the `CATALINA_OPTS` line: strip any existing `-Xmx…` token, then
  append ` -Xmx<size>` before the line's closing quote. Done with a temp-file
  `sed` (not `sed -i`, for macOS/Linux portability):

  ```bash
  sed -E "/CATALINA_OPTS:/{s/ -Xmx[0-9]+[mMgGkK]?//g;s/\"\$/ -Xmx${SIZE}\"/;}" \
    "$COMPOSE" > "$TMP" && mv "$TMP" "$COMPOSE"
  ```

  This works on any existing instance, including ones created before this
  feature (it edits the final `CATALINA_OPTS` string, not the placeholder).
  Assumption: `CATALINA_OPTS`'s value is a single double-quoted string that
  ends its line (true for the templates).
- `cd` to the instance dir and `docker compose up -d` — compose recreates the
  tomcat service because its environment changed; the db service is unchanged.
- Print a confirmation including the new `-Xmx<size>`.

A memory change briefly restarts Tomcat (DHIS2 re-boots; Flyway runs its
normal startup check). DB/volumes are preserved.

## 4. Broker (`d2-broker`)

- **Constants/helpers:**
  - `MEMORY_RE = re.compile(r"^[0-9]+[mMgG]$")`.
  - `mem_to_mb(size)` — pure: `"512m"→512`, `"2g"→2048`; `None` if unparseable.
    Used for the agent cap comparison. Unit-tested with the existing helpers.
- **Create (`POST /instances`):** optional `memory` field. If present, validate
  `MEMORY_RE` (else `400`); if scope is `agent`, reject when
  `mem_to_mb(memory)` exceeds the cap (`D2_BROKER_MAX_AGENT_MEMORY`, default
  `8g`) with `400`; then add `-m <memory>` to the `d2-instance-create` argv.
  If `memory` is omitted, no `-m` is passed and `d2-instance-create` applies
  its own 4g default (single source of truth for the default).
- **Change (new `POST /instances/<name>/memory`):** body `{"memory":"<size>"}`
  (required → `400` if missing). Same validation + agent-cap check, then
  `require_instance`, `check_no_active_job`, and submit a job (op `memory`)
  whose single step is `d2-set-memory <name> <size>`. `result` on success is
  the `GET /instances` element (like `start`).
- **Routing:** in `post_api`, add `if action == "memory": return
  self.set_memory_instance(name, scope)`.
- **Worker:** add `"memory"` to the op set that sets `result` to the matching
  `list_instances()` element (alongside `create`/`reset`/`start`/`upgrade`).

The agent cap uses a small inline check (no new gate abstraction):

```python
cap_mb = mem_to_mb(os.environ.get("D2_BROKER_MAX_AGENT_MEMORY", "8g"))
if scope != "all" and mem_to_mb(memory) > cap_mb:
    raise ApiError(400, f"requested heap exceeds the agent cap "
                        f"({os.environ.get('D2_BROKER_MAX_AGENT_MEMORY', '8g')})")
```

## 5. Configuration summary

| Variable | Default | Where | Meaning |
|---|---|---|---|
| `D2_DEFAULT_MEMORY` | `4g` | `d2-instance-create` | Heap for new instances when `-m` is omitted |
| `D2_BROKER_MAX_AGENT_MEMORY` | `8g` | `d2-broker` | Max heap an agent token may request |

## 6. Out of scope (YAGNI)

- No container `mem_limit`/`deploy.resources`.
- `GET /instances` does not report current heap (possible later follow-up).
- No "revert to JVM default / clear `-Xmx`" verb — a size is always set.
- `-Xms` is not configurable.

## 7. Docs

- `docs/broker-api.md` — `memory` field on create; new `POST
  /instances/<name>/memory` section; `op` enum gains `memory`; cheat-sheet row
  for `memory` and a note on the agent cap.
- `docs/broker.md` — endpoint table row; agent-scope note (memory allowed,
  capped by `D2_BROKER_MAX_AGENT_MEMORY`); the two new env vars in the config
  table.
- `skills/dhis2-instances/SKILL.md` — agent-facing: how to set memory at create
  and change it, the 4g default, and the cap (requests above it → `400`).
- `README.md` — the `-m` flag on `d2-instance-create` and the new
  `d2-set-memory` script in the script list + an example.

## 8. Testing

- **Unit (`bash-scripts-docker/test_d2_broker.py`):** `mem_to_mb` (`512m`,
  `2g`, `4g`, and unparseable inputs `""`, `"4"`, `"4gb"`) and that `MEMORY_RE`
  matches/rejects representative values.
- **Bash:** `bash -n` syntax check on the modified `d2-instance-create` and new
  `d2-set-memory`.
- **Live (against a throwaway instance on an alternate-port broker, installed
  broker untouched):**
  1. Create an instance with `-m 2g` (or `{"memory":"2g"}`) → assert the
     container's `CATALINA_OPTS` contains `-Xmx2g`
     (`docker exec <tomcat> printenv CATALINA_OPTS`).
  2. Create without `memory` → assert `-Xmx4g` (the default).
  3. `POST /instances/<name>/memory {"memory":"1g"}` → job succeeds, the
     instance's `docker-compose.yml` now has `-Xmx1g`, the tomcat container was
     recreated, and `printenv CATALINA_OPTS` shows `-Xmx1g`.
  4. Agent token requesting `memory` above the cap (e.g. `16g` with cap `8g`)
     → `400`; at/below the cap → accepted.
  5. Delete the throwaway instance.
