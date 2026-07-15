# Doris Analytics Backend (-a doris) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `d2-instance-create` (and the d2-broker API) create DHIS2 42+ instances with an Apache Doris analytics backend running as a per-instance compose service.

**Architecture:** A `doris` service (apache/doris:3.0.8-all, all-in-one, native arm64) is added to the tomcat10 compose template behind a compose profile (`analytics-doris`), activated by a `COMPOSE_PROFILES` line in the instance's `.env` — so every existing lifecycle script (`d2-startup`, `d2-shutdown`, `d2-instance-delete`, `d2-set-memory`) works unchanged. `d2-instance-create -a doris` copies Doris conf assets + the Postgres JDBC jar into the instance, appends four `analytics.*` lines to dhis.conf, and initializes Doris (create `analytics` database, set spill globals) after boot. The broker gains an `"analytics": "doris"` create parameter and an `analytics` field on instance elements. All facts come from the verified spike: `docs/doris/spike-findings-2026-07-14.md`.

**Tech Stack:** bash, Python 3 stdlib (d2-broker), docker compose ≥ 2.20 (`depends_on.required: false` verified working on the host's v5.2.0).

## Global Constraints

- `-a`/`analytics` only accepts `doris` and requires a version with DHIS2 major ≥ 42 (spike verified 2.42.5.1).
- Doris image pinned: `apache/doris:3.0.8-all` (matches dhis2-core CI's 3.0.7 line; arm64 verified).
- Memory (spike-verified floor on an 8 GB Docker VM): BE `mem_limit = 2800M`, FE `-Xmx768m`, container `mem_limit: 5500m`. At most one Doris instance per VM in practice.
- The Doris `analytics` database must be created by tooling (DHIS2 only creates the `pg_dhis` catalog).
- Postgres JDBC jar (42.7.5, from Maven Central) must be mounted into BOTH FE and BE `jdbc_drivers/`.
- Doris healthcheck must gate on BE aliveness (`SHOW BACKENDS` → `Alive: true`), not just FE.
- Spill globals must be set post-boot (3.0.x names): `enable_force_spill`, `enable_sort_spill`, `enable_agg_spill`, `parallel_pipeline_task_num=1`.
- Fail-before-side-effects: all new validation in `d2-instance-create` runs before ports/dirs/docker are touched (existing convention).
- Commit style: conventional short prefixes as in git log (`broker:`, `docs:`, `test(broker):`), each ending with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Doris template assets (fe.conf, be.conf)

**Files:**
- Create: `_templates/doris/fe.conf`
- Create: `_templates/doris/be.conf`

**Interfaces:**
- Produces: `_templates/doris/` directory that `setup.sh` copies to `$DHIS2_BASE/_templates/doris/` and `d2-instance-create` copies into `$DHIS2_BASE/<instance>/doris/`.

- [x] **Step 1: Write `_templates/doris/fe.conf`** — spike-verified content; `priority_networks` intentionally absent (the all-in-one entrypoint appends its own `127.0.0.1/24`, and a duplicate key would be ambiguous):

```
#####################################################################
## Doris FE config for DHIS2 test instances — based on dhis2-core
## docker/doris/fe.conf with a laptop-sized JVM heap.
## priority_networks is intentionally absent: the all-in-one image's
## entrypoint appends its own (127.0.0.1/24) on first boot.
#####################################################################

CUR_DATE=`date +%Y%m%d-%H%M%S`

# Log dir
LOG_DIR = ${DORIS_HOME}/log

# For jdk 17, this JAVA_OPTS will be used as default JVM options
JAVA_OPTS_FOR_JDK_17="-Dfile.encoding=UTF-8 -Djavax.security.auth.useSubjectCredsOnly=false -Xmx768m -Xms512m -XX:+HeapDumpOnOutOfMemoryError -XX:HeapDumpPath=$LOG_DIR -Xlog:gc*,classhisto*=trace:$LOG_DIR/fe.gc.log.$CUR_DATE:time,uptime:filecount=10,filesize=50M --add-opens=java.base/java.nio=ALL-UNNAMED --add-opens java.base/jdk.internal.ref=ALL-UNNAMED"

http_port = 8030
rpc_port = 9020
query_port = 9030
edit_log_port = 9010
arrow_flight_sql_port = -1

# Disable audit logging
enable_audit_log = false

sys_log_level = WARN
sys_log_mode = ASYNC

lower_case_table_names = 1
```

- [x] **Step 2: Write `_templates/doris/be.conf`**:

```
# Doris BE config for DHIS2 test instances — based on dhis2-core
# docker/doris/be.conf plus an absolute memory cap (the default is 90% of
# detected memory, which inside Docker Desktop can mean the whole VM).
# 2800M is the spike-verified floor for the SL-demo datavalue load; the
# compose service adds a 5500m container-level backstop.
mem_limit = 2800M

# Reduce glog verbosity (default is 10, higher = more verbose)
sys_log_verbose_level = 0

sys_log_level = WARN
sys_log_roll_num = 5
sys_log_roll_interval = DAY
```

- [x] **Step 3: Verify** — `grep -c priority_networks _templates/doris/fe.conf` prints `1` (only the comment mention; no active key: `grep -E '^priority_networks' _templates/doris/fe.conf` prints nothing) and `grep -q 'mem_limit = 2800M' _templates/doris/be.conf`.

- [x] **Step 4: Commit** — `git add _templates/doris && git commit -m "templates: Doris fe/be conf for -a doris instances"` (+ Co-Authored-By trailer).

---

### Task 2: doris service in the tomcat10 compose template

**Files:**
- Modify: `_templates/docker-compose-tomcat10.yml`

**Interfaces:**
- Consumes: `_templates/doris/*` (Task 1) mounted from the instance-local `./doris/` copy.
- Produces: compose profile name `analytics-doris` (exact string used by `.env` in Task 4 and broker detection in Task 5); container name pattern `<instance>-doris-1`; dev-net alias `dhis2-<instance>-doris`.

- [x] **Step 1: Add the `doris` service** between `db:` and `tomcat:`:

```yaml
  # Optional Apache Doris analytics backend (DHIS2 >= 42). Activated by
  # COMPOSE_PROFILES=analytics-doris in the instance .env (written by
  # d2-instance-create -a doris); inert otherwise. See
  # docs/doris/spike-findings-2026-07-14.md for the sizing rationale.
  doris:
    image: apache/doris:3.0.8-all
    restart: always
    profiles: ["analytics-doris"]
    environment:
      # Docker Desktop's VM has vm.max_map_count=262144 and refuses
      # per-container sysctls; this skips the BE's startup check.
      SKIP_CHECK_ULIMIT: "true"
    mem_limit: 5500m
    volumes:
      # fe.conf/be.conf must NOT be :ro — the entrypoint appends
      # priority_networks on first boot.
      - ./doris/fe.conf:/opt/apache-doris/fe/conf/fe.conf
      - ./doris/be.conf:/opt/apache-doris/be/conf/be.conf
      # DHIS2 hands Doris a bare driver filename; the jar must exist in
      # both FE and BE jdbc_drivers dirs.
      - ./doris/postgresql-42.7.5.jar:/opt/apache-doris/fe/jdbc_drivers/postgresql.jar:ro
      - ./doris/postgresql-42.7.5.jar:/opt/apache-doris/be/jdbc_drivers/postgresql.jar:ro
      - doris_fe_meta:/opt/apache-doris/fe/doris-meta
      - doris_be_storage:/opt/apache-doris/be/storage
    healthcheck:
      # FE answers SELECT 1 ~30-60s before the BE registers; gate on BE
      # aliveness so depends_on really means "Doris can take queries".
      test: ["CMD-SHELL", "mysql -h127.0.0.1 -P9030 -uroot -e 'SHOW BACKENDS\\G' 2>/dev/null | grep -q 'Alive: true'"]
      interval: 10s
      timeout: 5s
      retries: 60
      start_period: 30s
    networks:
      default: {}
      dev-net:
        aliases:
          - dhis2-${INSTANCE_NAME}-doris
```

- [x] **Step 2: Add the conditional dependency to `tomcat`** (replace the existing `depends_on` block):

```yaml
    depends_on:
      db:
        condition: service_healthy
      # Ignored when the analytics-doris profile is inactive (compose >=
      # 2.20 semantics, verified on this host); with the profile active,
      # tomcat waits for a query-ready Doris.
      doris:
        condition: service_healthy
        required: false
```

- [x] **Step 3: Add the volumes**:

```yaml
volumes:
  db_data:
  doris_fe_meta:
  doris_be_storage:
```

- [x] **Step 4: Verify both profile states render.** Render the template the way `d2-instance-create` does, then `docker compose config` both ways:

```bash
cd "$(mktemp -d)" && mkdir doris && touch doris/fe.conf doris/be.conf doris/postgresql-42.7.5.jar
sed "s/\${PG_PORT:-5432}/5433/g; s/\${HTTP_PORT:-8080}/9010/g; s/\${JAVA_MEM_OPTS}/ -Xmx2g/g; s/\${INSTANCE_NAME}/ctest/g" \
  ~/Repos/dhis2-docker-tools/_templates/docker-compose-tomcat10.yml > docker-compose.yml
docker network create dev-net 2>/dev/null || true
docker compose config --quiet && echo "OK without profile"
COMPOSE_PROFILES=analytics-doris docker compose config --quiet && echo "OK with profile"
docker compose config --services | grep -c doris   # expect 0
COMPOSE_PROFILES=analytics-doris docker compose config --services | grep -c doris  # expect 1
```

Expected: both `OK` lines, then `0` and `1`.

- [x] **Step 5: Commit** — `git commit -m "templates: optional Doris service behind analytics-doris profile (tomcat10)"`.

---

### Task 3: d2-lib.sh Doris helpers

**Files:**
- Modify: `bash-scripts-docker/d2-lib.sh` (append after `wait_for_db`)

**Interfaces:**
- Produces: `resolve_doris_container <instance>` (echoes container name or returns 1) and `doris_init <instance>` (creates the `analytics` DB + sets spill globals; returns non-zero on failure). Consumed by Task 4.

- [x] **Step 1: Append to `d2-lib.sh`:**

```bash
# Doris (analytics backend) container for an instance, if any.
resolve_doris_container() {
  local instance="$1"
  if docker ps -a --format '{{.Names}}' | grep -q "^${instance}-doris-1$"; then
    echo "${instance}-doris-1"
  elif docker ps -a --format '{{.Names}}' | grep -q "^${instance}_doris_1$"; then
    echo "${instance}_doris_1"
  else
    return 1
  fi
}

# One-time Doris initialization after the container is healthy. DHIS2
# creates its pg_dhis catalog itself but NOT the analytics database; and
# without spill, the aggregate datavalue load blows the laptop-sized BE
# memory cap (see docs/doris/spike-findings-2026-07-14.md). Globals persist
# in FE meta (a volume), but setting them is idempotent so we do it on
# every create.
doris_init() {
  local instance="$1"
  local container
  container=$(resolve_doris_container "$instance") || {
    echo "Error: Doris container for instance $instance not found" >&2
    return 1
  }
  docker exec "$container" mysql -h127.0.0.1 -P9030 -uroot \
    -e "CREATE DATABASE IF NOT EXISTS analytics;" || return 1
  # 3.0.x spill variable names (enable_spill does not exist there).
  docker exec "$container" mysql -h127.0.0.1 -P9030 -uroot \
    -e "SET GLOBAL enable_force_spill = true;
        SET GLOBAL enable_sort_spill = true;
        SET GLOBAL enable_agg_spill = true;
        SET GLOBAL parallel_pipeline_task_num = 1;" || return 1
}
```

- [x] **Step 2: Verify syntax** — `bash -n bash-scripts-docker/d2-lib.sh` (no output).

- [x] **Step 3: Commit** — `git commit -m "lib: resolve_doris_container + doris_init helpers"`.

---

### Task 4: d2-instance-create -a doris

**Files:**
- Modify: `bash-scripts-docker/d2-instance-create`

**Interfaces:**
- Consumes: `doris_init` (Task 3), `_templates/doris/*` (Task 1), profile name `analytics-doris` (Task 2).
- Produces: `-a <backend>` CLI flag; instance-side artifacts: `$DHIS2_HOME/doris/{fe.conf,be.conf,postgresql-42.7.5.jar}`, `$DHIS2_HOME/.env`, dhis.conf `analytics.*` block. The broker (Task 5) passes `-a doris` through.

- [x] **Step 1: Add the option.** Usage text gets `echo "  -a <backend>    Analytics database backend: doris (requires -v with DHIS2 >= 42)"`; defaults gain `ANALYTICS=""`; getopts string becomes `"v:l:s:p:g:f:t:m:a:"` with case arm `a) ANALYTICS=$OPTARG ;;`.

- [x] **Step 2: Validate before any side effects** (immediately after the Tomcat/-t reconciliation block, before port selection):

```bash
# Validate the analytics backend request. Done here — before port
# selection, directory creation, or docker — so error paths have no side
# effects. Doris analytics needs DHIS2 >= 42 (which also implies Tomcat 10).
if [ -n "$ANALYTICS" ]; then
  if [ "$ANALYTICS" != "doris" ]; then
    echo "Error: -a must be 'doris' (got '$ANALYTICS')" >&2
    exit 1
  fi
  if [ -z "$VERSION" ]; then
    echo "Error: -a doris requires -v with a DHIS2 version >= 42" >&2
    exit 1
  fi
  if [ "$DHIS_MAJOR" -lt 42 ]; then
    echo "Error: -a doris requires DHIS2 42 or later (got $VERSION, major $DHIS_MAJOR)" >&2
    exit 1
  fi
fi
```

- [x] **Step 3: Materialize Doris assets** (after the existing "Copy template files" block):

```bash
# Doris backend: copy conf assets, ensure the Postgres JDBC jar (cached in
# _templates/doris/, downloaded from Maven Central on first use), activate
# the compose profile via .env, and point DHIS2 at Doris in dhis.conf.
if [ "$ANALYTICS" = "doris" ]; then
  PGJDBC_VERSION="42.7.5"
  PGJDBC_JAR="postgresql-${PGJDBC_VERSION}.jar"
  PGJDBC_CACHE="${DHIS2_BASE}/_templates/doris/${PGJDBC_JAR}"
  if [ ! -f "$PGJDBC_CACHE" ]; then
    echo "Downloading Postgres JDBC driver ${PGJDBC_VERSION} (for the Doris catalog)..."
    PGJDBC_URL="https://repo1.maven.org/maven2/org/postgresql/postgresql/${PGJDBC_VERSION}/${PGJDBC_JAR}"
    curl -fSL --no-progress-meter -o "${PGJDBC_CACHE}.tmp" "$PGJDBC_URL"
    EXPECTED_SHA=$(curl -fsSL "${PGJDBC_URL}.sha256")
    ACTUAL_SHA=$(shasum -a 256 "${PGJDBC_CACHE}.tmp" | cut -d' ' -f1)
    if [ "$EXPECTED_SHA" != "$ACTUAL_SHA" ]; then
      echo "Error: JDBC driver checksum mismatch (expected $EXPECTED_SHA, got $ACTUAL_SHA)" >&2
      rm -f "${PGJDBC_CACHE}.tmp"
      exit 1
    fi
    mv "${PGJDBC_CACHE}.tmp" "$PGJDBC_CACHE"
  fi
  mkdir -p "$DHIS2_HOME/doris"
  cp "${DHIS2_BASE}/_templates/doris/fe.conf" "$DHIS2_HOME/doris/fe.conf"
  cp "${DHIS2_BASE}/_templates/doris/be.conf" "$DHIS2_HOME/doris/be.conf"
  cp "$PGJDBC_CACHE" "$DHIS2_HOME/doris/${PGJDBC_JAR}"
  # Activates the doris service for every compose invocation in this
  # directory (startup, shutdown, delete, set-memory) with no flag changes.
  echo "COMPOSE_PROFILES=analytics-doris" > "$DHIS2_HOME/.env"
  cat >> "$DHIS2_HOME/DHIS2_HOME/dhis.conf" << 'DORISCONF'

# Apache Doris analytics database (added by d2-instance-create -a doris)
analytics.database = doris
analytics.connection.url = jdbc:mysql://doris:9030/analytics?useUnicode=true&characterEncoding=UTF-8&useSSL=false
analytics.connection.username = root
analytics.connection.password =
DORISCONF
fi
```

Note: the jar cache lives under `$DHIS2_BASE/_templates/doris/`, which `setup.sh install/update` also populates with fe.conf/be.conf — `copy_new_templates` copies files, so also change `setup.sh`'s `copy_templates`/`copy_new_templates` to use `cp -R` so the `doris/` subdirectory is included:
in `copy_templates`: `cp -Rf "$SCRIPT_DIR/_templates/"* "$DHIS2_BASE/_templates/"`; in `copy_new_templates`, the existing per-entry loop already iterates `"$SCRIPT_DIR/_templates/"*` — change its `cp "$tpl" ...` to `cp -R "$tpl" ...` (a new `doris` dir is copied whole; an existing one is left untouched by the `[ ! -e ]` guard).

- [x] **Step 4: Initialize Doris after boot.** `docker compose up -d` already blocks until the Doris healthcheck passes (verified compose behavior), so right after the existing `wait_for_db` block add:

```bash
# Doris: create the analytics database (DHIS2 only creates its catalog)
# and set laptop-sized spill globals. compose up already waited for the
# Doris healthcheck (BE alive), so this can run immediately. Must happen
# before the WAR boots, or DHIS2's first analytics run would 404 its own
# database.
if [ "$ANALYTICS" = "doris" ]; then
  echo "Initializing Doris analytics database..."
  if ! doris_init "$INSTANCE"; then
    echo "Error: Doris initialization failed" >&2
    exit 1
  fi
fi
```

- [x] **Step 5: Extend the create banner and final output.** Banner: `[ -n "$ANALYTICS" ] && echo "  Analytics DB:    $ANALYTICS"`. Final output, after the PostgreSQL line:

```bash
if [ "$ANALYTICS" = "doris" ]; then
  echo "  Doris:        internal doris:9030 (analytics DB; MySQL protocol, root, empty password)"
  echo "                dev-net: dhis2-$INSTANCE-doris:9030"
fi
```

- [x] **Step 6: Verify validation fails cleanly (no side effects):**

```bash
cd bash-scripts-docker
./d2-instance-create -a doris atest1 2>&1                 # expect: requires -v error, exit 1
./d2-instance-create -a doris -v 2.41 atest1 2>&1          # expect: requires 42+ error, exit 1
./d2-instance-create -a bogus -v 42 atest1 2>&1            # expect: must be 'doris' error, exit 1
ls "$DHIS2_BASE" | grep -c atest1                          # expect 0 (nothing created)
bash -n d2-instance-create
```

- [x] **Step 7: Commit** — `git commit -m "d2-instance-create: -a doris analytics backend (compose profile + init)"`.

---

### Task 5: broker support (TDD)

**Files:**
- Modify: `bash-scripts-docker/test_d2_broker.py` (tests first)
- Modify: `bash-scripts-docker/d2-broker`

**Interfaces:**
- Consumes: `-a doris` flag (Task 4); `.env` written with `COMPOSE_PROFILES=analytics-doris` (Task 4).
- Produces: `POST /instances` body key `"analytics"`; `GET /instances` element key `"analytics"` (`"doris"` or `null`); env var `D2_BROKER_MAX_AGENT_DORIS` (default 1); pure helpers `parse_analytics_profiles(env_text)` and `analytics_request_error(analytics, version)`.

- [x] **Step 1: Write failing tests** (append to `test_d2_broker.py`):

```python
class ParseAnalyticsProfiles(unittest.TestCase):
    def test_doris_profile(self):
        self.assertEqual(
            broker.parse_analytics_profiles("COMPOSE_PROFILES=analytics-doris\n"),
            "doris")

    def test_doris_among_multiple_profiles(self):
        self.assertEqual(
            broker.parse_analytics_profiles("COMPOSE_PROFILES=foo, analytics-doris ,bar"),
            "doris")

    def test_no_profile_line(self):
        self.assertIsNone(broker.parse_analytics_profiles("SOMETHING=else\n"))

    def test_other_profiles_only(self):
        self.assertIsNone(
            broker.parse_analytics_profiles("COMPOSE_PROFILES=debug"))

    def test_empty_and_none(self):
        self.assertIsNone(broker.parse_analytics_profiles(""))
        self.assertIsNone(broker.parse_analytics_profiles(None))


class AnalyticsRequestError(unittest.TestCase):
    def test_valid_doris_42(self):
        self.assertIsNone(broker.analytics_request_error("doris", "42"))
        self.assertIsNone(broker.analytics_request_error("doris", "2.42.4"))
        self.assertIsNone(broker.analytics_request_error("doris", "2.43"))

    def test_unknown_backend(self):
        self.assertIn("doris", broker.analytics_request_error("clickhouse", "42"))

    def test_missing_version(self):
        self.assertIn("version", broker.analytics_request_error("doris", None))
        self.assertIn("version", broker.analytics_request_error("doris", ""))

    def test_major_below_42(self):
        self.assertIn("42", broker.analytics_request_error("doris", "2.41"))
        self.assertIn("42", broker.analytics_request_error("doris", "40"))

    def test_non_numeric_major(self):
        self.assertIsNotNone(broker.analytics_request_error("doris", "abc"))
```

- [x] **Step 2: Run to verify failure** — `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v 2>&1 | tail -3` → `AttributeError: ... parse_analytics_profiles`.

- [x] **Step 3: Implement in `d2-broker`** (after `extract_target_major`):

```python
ANALYTICS_BACKENDS = ("doris",)
DORIS_PROFILE = "analytics-doris"


def parse_analytics_profiles(env_text):
    """Analytics backend implied by a compose .env's COMPOSE_PROFILES line:
    'doris' if the analytics-doris profile is active, else None."""
    for line in (env_text or "").splitlines():
        if line.strip().startswith("COMPOSE_PROFILES="):
            profiles = line.strip().split("=", 1)[1].split(",")
            if DORIS_PROFILE in (p.strip() for p in profiles):
                return "doris"
    return None


def instance_analytics(name):
    """Analytics backend of an instance ('doris' or None), read from its
    .env — works for stopped instances, unlike container inspection."""
    try:
        return parse_analytics_profiles(
            (dhis2_base() / name / ".env").read_text())
    except OSError:
        return None


def analytics_request_error(analytics, version):
    """Validation message for an analytics-backend create request, else
    None. Doris needs a resolvable DHIS2 major >= 42, which also excludes
    war_url/war_file creates (their major is unknowable)."""
    if analytics not in ANALYTICS_BACKENDS:
        return f"analytics must be one of: {', '.join(ANALYTICS_BACKENDS)}"
    if not version:
        return "analytics requires a 'version' with DHIS2 major >= 42"
    major = extract_target_major(version)
    if major is None or major < 42:
        return (f"analytics backend '{analytics}' requires DHIS2 42 or "
                f"later (got '{version}')")
    return None
```

In `list_instances()`, add to the element dict (after `"agent_managed"`): `"analytics": instance_analytics(inst),`.

In `create_instance()`, after the version validation block and before the war_url/war_file block:

```python
        analytics = body.get("analytics")
        if analytics:
            err = analytics_request_error(analytics, version)
            if err:
                raise ApiError(400, err)
            if body.get("war_url") or body.get("war_file"):
                raise ApiError(400, "analytics cannot be combined with "
                                    "war_url/war_file (a 'version' is how "
                                    "the 42+ requirement is checked)")
            if scope == "agent":
                # A Doris sidecar is a ~5.5 GB tenant; one is all a
                # laptop-sized Docker VM fits (see spike findings doc).
                cap = int(os.environ.get("D2_BROKER_MAX_AGENT_DORIS", 1))
                doris_owned = [i["name"] for i in list_instances()
                               if i["agent_managed"] and i["analytics"]]
                if len(doris_owned) >= cap:
                    raise ApiError(409,
                                   f"agent Doris-instance cap reached ({cap}); "
                                   f"Doris needs ~5.5 GB RAM per instance; "
                                   f"delete one of: {', '.join(doris_owned)}")
            argv += ["-a", str(analytics)]
```

- [x] **Step 4: Run tests** — `python3 -m unittest test_d2_broker -v 2>&1 | tail -3` → `OK` (all existing tests still pass).

- [x] **Step 5: Commit** — `git commit -m "broker: analytics=doris create param, analytics field, agent Doris cap"`.

---

### Task 6: docs + skill

**Files:**
- Modify: `docs/broker.md` — config table row `D2_BROKER_MAX_AGENT_DORIS | 1 | Cap on agent-owned Doris-enabled instances`; `POST /instances` body gains `"analytics"?`; new short section "Doris analytics backend" (42+ only, one per VM, what v42 routes to Doris, pointer to spike findings).
- Modify: `docs/broker-api.md` — same contract additions: `analytics` in create body (admin+agent, `400` below 42, `409` at agent cap), `analytics` field on instance elements.
- Modify: `README.md` — `-a doris` in the create examples + one-line description; also fix the two broken `broker.md` links to `docs/broker.md` and mention `setup.sh` in Quick Setup (pre-existing doc drift, one-line fixes while here).
- Modify: `skills/dhis2-instances/SKILL.md` — new "Doris analytics backend" section: request with `"analytics":"doris"` (version 42+), expect `analytics` field, ~5.5 GB footprint / cap of 1, v42 routes only aggregate+completeness to Doris (events/enrollment/TE stay in Postgres until 43), verify via `POST /api/resourceTables/analytics` then compare `SELECT count(*) FROM analytics.analytics` over `dhis2-<name>-doris:9030` (mysql protocol, root, empty password) — and the Tomcat-restart-after-Doris-recreate gotcha.

- [x] **Step 1: Write all four doc changes** (content per file summary above; keep each addition under ~25 lines, matching existing doc voice).
- [x] **Step 2: Verify** — `grep -rn "analytics" docs/broker.md docs/broker-api.md README.md skills/dhis2-instances/SKILL.md | grep -ci doris` ≥ 8; `grep -c "docs/broker.md" README.md` = 2.
- [x] **Step 3: Commit** — `git commit -m "docs: -a doris / analytics param in README, broker docs, dhis2-instances skill"`.

---

### Task 7: end-to-end verification

**Files:** none (verification only; uses the host Docker + broker)

- [x] **Step 1: Free memory** — `d2-shutdown doris-spike` (the spike instance's 5.5 GB Doris; instance is kept on disk).
- [x] **Step 2: Create via the new flag** — `d2-instance-create -v 42 -m 2g -a doris -s "$DHIS2_BASE/_seeds/dhis2-db-sierra-leone_v42.sql.gz" doris-e2e`; expect the banner to show `Analytics DB: doris`, compose to wait for Doris health, `Initializing Doris analytics database...`, and a successful create.
- [x] **Step 3: Verify wiring** — `SHOW CATALOGS` in `doris-e2e-doris-1` shows `pg_dhis` after DHIS2 boots; `GET /api/system/info` answers 200.
- [x] **Step 4: Run the export** — `POST /api/resourceTables/analytics` as `local_admin:district`; poll `/api/system/tasks/ANALYTICS_TABLE` to completion; expect `Analytics tables updated`.
- [x] **Step 5: Verify data** — `SELECT count(*) FROM analytics.analytics` in Doris = 4,902,923 (same seed as the spike baseline); one `/api/analytics` spot query returns values.
- [x] **Step 6: Broker surface** — restart the broker (`launchctl kickstart -k gui/$(id -u)/org.dhis2.d2-broker`), then with the admin token: `GET /instances` shows `"analytics": "doris"` for doris-e2e and `null` for doris-spike (which has no .env); `POST /instances` with `{"name":"agent-x","version":"2.41","analytics":"doris"}` returns the 400.
- [x] **Step 7: Clean up** — `d2-instance-delete doris-e2e`; leave `doris-spike` stopped (user's call to restart or delete).
- [x] **Step 8: Mark plan checkboxes done; commit** — `git commit -m "docs(plan): doris analytics backend plan executed"`.
