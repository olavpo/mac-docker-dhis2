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
- Expose all of the above over a local HTTP API (`d2-broker`) for other
  clients — AI agent sandboxes, menu bar widgets, dashboards. See [broker.md](broker.md).

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

3. Ensure templates exist:

```text
   $DHIS2_BASE/_templates/
     dhis.conf
     log4j2.xml
     server.xml
     docker-compose-tomcat9.yml
     docker-compose-tomcat10.yml
```

4. (Optional) Put scripts on your PATH, e.g.:

```bash
   ln -s "$PWD/bash-scripts-docker/d2-instance-create" /usr/local/bin/d2-instance-create
   # repeat for others as needed
```

---

## Core Scripts (Short Version)

- **Instance lifecycle**
  - `d2-instance-create` — create a new DHIS2 instance (Tomcat + Postgres)
  - `d2-instance-delete` — delete an instance and its Docker volumes
  - `d2-startup` / `d2-shutdown` — start/stop an existing instance

- **Database**
  - `d2-db-backup` — backup DB to `$DHIS2_BASE/_backups/<instance>/...`
  - `d2-db-restore` — restore DB from `.sql`, `.sql.gz`, or `.pgc`
  - `d2-db-version` — restore a backup into a temp instance and read `flyway_schema_history`
  - `d2-psql` — open `psql` inside the DB container

- **Application & info**
  - `d2-deploy-war` — deploy a WAR by version, URL, or local file
  - `d2-info` — list instances, ports, DB version, and status
  - `d2-logtail` — `docker logs -f` for Tomcat

- **HTTP API**
  - `d2-broker` — token-authenticated local HTTP API over the scripts above,
    with a restricted scope for AI agent sandboxes ([broker.md](broker.md))

---

## Basic Examples

### Create an instance

```bash
d2-instance-create -v 2.42.4 -p 9010 -g 5433 myinstance
# Access: http://localhost:9010
# DB: localhost:5433 (user: dhis, password: dhis, db: dhis2)
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
```

Restore:

```bash
d2-db-restore myinstance $DHIS2_BASE/_backups/myinstance/myinstance_20240101-120000_v41.sql.gz
```

---

### Detect DHIS2 version from a backup

```bash
d2-db-version /path/to/backup.sql.gz
# DHIS2 Version: 2.41.7
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
