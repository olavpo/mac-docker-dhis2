# DHIS2 Docker Tools (UNSUPPORTED)

A small collection of bash scripts for running **DHIS2 in Docker** on a **personal machine** for **testing and development only**.

> **Important**
>
> - **Not supported** by DHIS2 or any organization.
> - **No guarantees** of correctness, stability, or security.
> - Intended only for **single‑user workstations** (not servers).
> - Only **lightly tested on Apple Silicon (ARM64, macOS)**.
> - Use at your own risk.

---

## What This Provides

Scripts to help you:

- Create and delete DHIS2 Docker instances
- Start and stop instances
- Backup and restore databases
- Deploy DHIS2 WAR files
- Check instance status
- Inspect logs and connect to PostgreSQL
- Detect DHIS2 version from a database backup
- Optionally attach a dedicated **Apache Doris analytics database** to an
  instance (DHIS2 ≥ 42): `d2-instance-create -a doris`
- Expose all of the above over a local HTTP API (`d2-broker`) for other
  clients — AI agent sandboxes, menu bar widgets, dashboards. See [docs/broker.md](docs/broker.md).

All scripts are in `bash-scripts-docker/` and expect:

- Docker + Docker Compose
- PostgreSQL client tools (`psql`, `pg_dump`, `pg_restore`)
- `DHIS2_BASE` env var pointing to your DHIS2 base directory

---

## Quick Setup

1. Clone:

```bash
   git clone https://github.com/your-repo/dhis2-docker-tools.git
   cd dhis2-docker-tools
```

2. Set base directory (example):

```bash
   export DHIS2_BASE=$HOME/dhis2-docker
```

3. Run the setup script — copies templates to `$DHIS2_BASE/_templates/` and
   symlinks the scripts into `/usr/local/bin`:

```bash
   ./setup.sh install         # first time
   ./setup.sh update          # after pulling changes
   ./setup.sh sync-templates  # overwrite installed templates with the repo versions
```

`update` never overwrites an installed template, since it may carry local
customisations. It reports which ones differ from the repo instead — run
`sync-templates` to overwrite them. The scripts read
`$DHIS2_BASE/_templates/`, not the repo, so a template edit does nothing until
it is synced. Existing instances keep the compose file they were created with;
only newly created instances read the templates.

---

## Core Scripts (Short Version)

- **Instance lifecycle**
  - `d2-instance-create` — create a new DHIS2 instance (Tomcat + Postgres)
  - `d2-instance-delete` — delete an instance and its Docker volumes
  - `d2-startup` / `d2-shutdown` — start/stop an existing instance

- **Database**
  - `d2-db-backup` — backup DB to `$DHIS2_BASE/_backups/<instance>/...`
  - `d2-db-restore` — restore DB from `.sql`, `.sql.gz`, or `.pgc`; disables all scheduled jobs in the restored DB
  - `d2-local-admin` — ensure/remove the known `local_admin` superuser in an instance DB
  - `d2-db-version` — restore a backup into a temp instance and read `flyway_schema_history`
  - `d2-psql` — open `psql` inside the DB container

- **Application & info**
  - `d2-deploy-war` — deploy a WAR by version, URL, or local file
  - `d2-set-memory` — set the Tomcat max heap (`-Xmx`) on an instance + recreate Tomcat
  - `d2-switch-tomcat` — move an instance from Tomcat 9 to 10, keeping its database (needed for a 2.41 → 2.42 upgrade; deploy the new WAR afterwards)
  - `d2-info` — list instances, ports, DB version, and status
  - `d2-logtail` — `docker logs -f` for Tomcat

- **HTTP API**
  - `d2-broker` — token-authenticated local HTTP API over the scripts above,
    with a restricted scope for AI agent sandboxes ([docs/broker.md](docs/broker.md))

---

## Basic Examples

### Create an instance

```bash
d2-instance-create -v 2.42.4 -p 9010 -g 5433 myinstance
# Access: http://localhost:9010
# DB: localhost:5433 (user: dhis, password: dhis, db: dhis2)

d2-instance-create -v 2.42 -m 6g myinstance   # 6 GB max heap (default is 4g)

d2-instance-create -v 2.41 myinstance   # Tomcat auto-selected (2.41 -> Tomcat 9)

d2-instance-create -v 42 -z Africa/Lagos myinstance   # server time zone (default Etc/UTC)

d2-instance-create -v 42 -a doris myinstance   # with a dedicated Apache Doris
# analytics database (DHIS2 >= 42 only; adds a ~5.5 GB Doris container —
# see docs/doris/spike-findings-2026-07-14.md for what runs in Doris per version)
```

If you omit `-p` / `-g`, ports are auto‑selected.

---

### Deploy a DHIS2 WAR

From a version:

```bash
d2-deploy-war -v 2.42.4 myinstance
```

From a local file:

```bash
d2-deploy-war -f /path/to/custom.war myinstance
```

---

### Backup and restore database

Backup:

```bash
d2-db-backup myinstance
# writes to: $DHIS2_BASE/_backups/myinstance/<timestamp>_vXX.sql.gz

d2-db-backup -l pre-upgrade myinstance
# optional label folded into the name: myinstance_<timestamp>_vXX_pre-upgrade.sql.gz
```

Backups exclude the analytics tables (`analytics` and `analytics_*`, which
also covers the `analytics_rs_*` resource tables). They hold derived data and
often dominate the database size; DHIS2 rebuilds them on the next analytics
run. A restored instance therefore has no analytics until you run analytics on
it. If any SQL view reads an analytics table, `d2-db-backup` warns and names
the view — that view is not usable after a restore until analytics is rebuilt.

Restore:

```bash
d2-db-restore myinstance $DHIS2_BASE/_backups/myinstance/myinstance_20240101-120000_v41.sql.gz
```

Every restore disables **all** scheduled jobs (`jobconfiguration.enabled = false`)
in the restored database — analytics runs, predictors, sync jobs and built-in
housekeeping alike — so nothing suddenly triggers on a local instance.
Re-enable individual jobs in the Scheduler app if you need them.

---

### Detect DHIS2 version from a backup

```bash
d2-db-version /path/to/backup.sql.gz
# Major Version: 41
```

---

### Check instances and logs

List instances:

```bash
d2-info
```

Tail logs:

```bash
d2-logtail myinstance
```

---

## Warnings and Limitations

- Only tested on **macOS / Apple Silicon (ARM64)**.
- No load‑balancing, clustering, or production hardening.
- No automated migration or upgrade safety checks.
- Changes to DHIS2 Docker images or releases may break these scripts.

If you’re running **anything production‑like**, use officially supported deployment methods instead.

---

## License & Support

- License: see `LICENSE` (e.g. BSD 3‑Clause).
- Support: **none**. Use the GitHub issue tracker at your own discretion, but there is **no guarantee of response or fixes**.
