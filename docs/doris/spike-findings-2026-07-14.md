# Doris analytics spike — findings (2026-07-14)

Goal: prove that a per-instance Apache Doris analytics backend works with a
d2-instance-create-style DHIS2 2.42 Docker instance on Apple Silicon, and
collect the facts needed to build `-a doris` support into the tooling and
broker. **Result: proven end-to-end.**

Spike instance: `doris-spike` (DHIS2 2.42.5.1, Tomcat 10, SL demo v42 seed,
`-Xmx2g`), hand-edited compose + dhis.conf. Left running for inspection.

## What was proven

- **DHIS2 2.42 + Doris works in one compose project.** DHIS2 created its
  `pg_dhis` JDBC catalog in Doris on boot; Doris read 4,933,875 `datavalue`
  rows from Postgres through it (hostname `db` resolves because both are on
  the compose default network — critical on 2.42, where the catalog URL is
  `connection.url` verbatim with no override key; 2.43 adds
  `doris.catalog.connection.url`).
- **Analytics export completed: 1:38** (Postgres-only baseline on the same
  instance: 5:46). `analytics.analytics` in Doris = **4,902,923 rows —
  identical to the Postgres baseline**. Caveat: laptop single-node numbers,
  not a benchmark; the aggregate `analytics` table populate itself took 36s.
- **Queries are actually served by Doris**: a standard `/api/analytics`
  query returned 140 rows identical to the Postgres baseline, and still
  returned correct values after the Postgres `analytics` table was renamed
  away (then restored).
- **root / empty password works** (`analytics.connection.username=root`,
  empty password) — same as dhis2-core's own CI config.

## What v42 actually routes to Doris (empirical, 2.42.5.1)

| Table type | Destination |
|---|---|
| `analytics` (aggregate data values) | **Doris** |
| `analytics_completeness`, `analytics_completenesstarget`, `analytics_orgunittarget` | **Doris** |
| `analytics_rs_*` resource tables | Built in Postgres, **replicated to Doris** |
| `analytics_event_*` | Postgres only |
| `analytics_enrollment_*` | Postgres only |
| `analytics_te*` (tracked entity) | Postgres only |

Note: the DAC2026 slides say v42 covers "aggregate data and event analytics";
on 2.42.5.1 the event tables are demonstrably not created in Doris. Re-verify
on 2.43 (where enrollment/tracker support is documented and
`JdbcTrackedEntityAnalyticsTableManager` exists).

## Working configuration

**Image:** `apache/doris:3.0.8-all` (all-in-one FE+BE, single container,
native linux/arm64 — verified via `docker manifest inspect`). Chosen over
the fe/be pair dhis2-core's CI uses because the all-in-one self-registers
its BE on 127.0.0.1 and needs **no static IPs** — the fe/be images require
literal IPs in `FE_SERVERS`/`BE_ADDR`, which would force per-instance subnet
allocation. Chosen over `4.0.3-all-slim` (1.1GB vs 4.5GB) to match DHIS2
CI's pinned `DORIS_VERSION=3.0.7`. No 3.0.x slim tag exists.

**Compose service (as used, working):**

```yaml
  doris:
    image: apache/doris:3.0.8-all
    restart: always
    environment:
      SKIP_CHECK_ULIMIT: "true"   # Docker Desktop VM has max_map_count=262144, not raisable per-container
    mem_limit: 5500m
    volumes:
      - ./doris/fe.conf:/opt/apache-doris/fe/conf/fe.conf        # NOT :ro — entrypoint appends priority_networks
      - ./doris/be.conf:/opt/apache-doris/be/conf/be.conf        # NOT :ro
      - ./doris/postgresql-42.7.5.jar:/opt/apache-doris/fe/jdbc_drivers/postgresql.jar:ro
      - ./doris/postgresql-42.7.5.jar:/opt/apache-doris/be/jdbc_drivers/postgresql.jar:ro
      - doris_fe_meta:/opt/apache-doris/fe/doris-meta
      - doris_be_storage:/opt/apache-doris/be/storage
    healthcheck:   # NOTE: passes before the BE is registered; see gotcha 3
      test: ["CMD-SHELL", "mysql -h127.0.0.1 -P9030 -uroot -e 'SELECT 1' >/dev/null 2>&1 || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 30s
```

`fe.conf`: dhis2-core reference minus `priority_networks`, with
`-Xmx768m -Xms512m`, `lower_case_table_names = 1`, audit log off.
`be.conf`: `mem_limit = 2800M` + quiet logging.
Tomcat gets `depends_on: doris: condition: service_healthy`.

**dhis.conf additions:**

```
analytics.database = doris
analytics.connection.url = jdbc:mysql://doris:9030/analytics?useUnicode=true&characterEncoding=UTF-8&useSSL=false
analytics.connection.username = root
analytics.connection.password =
```

**Postgres JDBC jar:** DHIS2 passes `driver_url = "postgresql.jar"` (a bare
filename) when creating the catalog; Doris resolves it against its local
`jdbc_drivers` dir in **both** FE and BE. The jar must be mounted into both
paths. Source used: dhis2-core `docker/doris/postgresql-42.7.5.jar` (also on
Maven Central).

**One-time init after Doris is up (DHIS2 does NOT do this):**

```
mysql -h127.0.0.1 -P9030 -uroot -e "CREATE DATABASE IF NOT EXISTS analytics"
```

## Gotchas hit (each cost a failed run)

1. **BE memory.** `mem_limit = 1536M` → `MEM_LIMIT_EXCEEDED` cancelling the
   4.9M-row datavalue load (the load tracker alone wants ~1.4GB). Even at
   2800M it failed while the *container* cap was 4g (Doris cancels when
   container-available memory dips under its low-water mark; jemalloc holds
   ~1.5GB freed memory as cache during the burst). **Working floor on this
   8GB Docker VM: BE `mem_limit = 2800M` inside a 5.5g container**, with
   `SET GLOBAL enable_force_spill/enable_sort_spill/enable_agg_spill = true`
   and `parallel_pipeline_task_num = 1` (3.0.x names; the `enable_spill`
   variable doesn't exist on 3.0.8).
2. **Stale connection pool.** Recreating the Doris container while Tomcat
   kept running → next export failed instantly with "Communications link
   failure" (dead pooled connection). Any Doris container recreate needs a
   Tomcat restart (or pool test-on-borrow configuration).
3. **Container recreation semantics.** Adding `depends_on` to a running
   tomcat doesn't recreate it (`docker compose up -d` sees no container-config
   change) — dhis.conf changes only load on an explicit
   `--force-recreate`/`restart`. Also the FE healthcheck (`SELECT 1`) passes
   ~30–60s before the BE registers as alive; a better healthcheck greps
   `SHOW BACKENDS` for `Alive: true`.

## Implications for the tooling design

- The all-in-one image removes the static-IP/subnet problem entirely — the
  planned "compose profile + `.env` `COMPOSE_PROFILES`" template design works
  unchanged, including multiple concurrent Doris instances.
- `d2-instance-create -a doris` must: copy `doris/` conf dir + jar into the
  instance, write the `.env` profile, append the 4 dhis.conf lines, wait for
  Doris health (BE-alive check), run the `CREATE DATABASE` init, set the
  spill/parallelism globals, and only then start/restart Tomcat.
- The jar should ship in `_templates/` (1.1MB) or be curl'd from Maven
  Central at create time.
- Memory gating matters more than anticipated: a Doris instance adds ~5.5GB
  to the footprint. The broker should cap agent-scoped Doris instances at 1
  and/or require an explicit memory budget; docs should say "8GB Docker VM
  fits exactly one Doris-enabled instance with a 2g DHIS2 heap".
- Healthcheck for the template: `mysql ... -e "SHOW BACKENDS\G" | grep -q
  'Alive: true'`.
- v42 value proposition is aggregate analytics only; events/enrollment/TE
  stay in Postgres until 2.43 — document this in the skill so agents don't
  chase "missing" event tables in Doris.
