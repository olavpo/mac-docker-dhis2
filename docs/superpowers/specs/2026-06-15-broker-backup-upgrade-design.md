# d2-broker: backup + upgrade endpoints — design

Implements the two new endpoints from
[`docs/proposed-broker-endpoints.md`](../../proposed-broker-endpoints.md):
`POST /instances/<name>/backup` and `POST /instances/<name>/upgrade`. The
proposal's item #0 (stopped instances disappearing from `GET /instances`) is
already fixed — `list_instances()` enumerates by instance directory — so this
design covers only the two new endpoints plus the small supporting changes they
need.

Broker source: `bash-scripts-docker/d2-broker` (Python 3, stdlib only). Both
endpoints follow the existing **202 + job** model, run on the single global
worker, and reject a second op on an instance with an active job with `409`.

## Decisions (resolved during brainstorming)

- **Backup of a stopped instance:** require the DB to be running. No transient
  container start. `d2-db-backup` already errors clearly ("start the instance
  first") when the DB container is not running, and that failure surfaces as a
  `failed` job.
- **Backup retention:** none. Backups accumulate under `_backups/<name>/` and
  are managed by the user. No pruning, no config knob.
- **Upgrade `tomcat` field:** defer servlet-container migration. Accept the
  field only when it equals the instance's current Tomcat major; otherwise
  `400`.
- **Upgrade transition policy:** basic guards on the `version` form only —
  reject downgrades and major-version skips. `war_url`/`war_file` skip the guard
  (the broker can't read the WAR's version).
- **Upgrade result `dhis2_major_version`:** best-effort. `d2-deploy-war` returns
  right after `docker start`, before Flyway finishes migrating, so the value may
  still read the pre-upgrade major. v1 returns it best-effort and documents that
  the client should re-poll `GET /instances?full=1` once the instance is up.
  No bounded wait-for-migration step in v1.

## 1. `POST /instances/<name>/backup`

Create a `pg_dump` backup of the instance's database. Returns **202 + job**.

**Auth / scope:** admin only. An agent token → `403` (backups are already
admin-scoped in `GET /seeds`).

**Body (all optional):**

```json
{ "label": "pre-upgrade" }
```

- `label` (if present) must match `^[a-z0-9][a-z0-9_-]{0,39}$`.

**Preconditions:**

- Instance exists (`require_instance`) → else `404`.
- No active job for the instance → else `409`.
- DB container running. Not pre-checked by the broker; `d2-db-backup` already
  verifies the DB is running and exits non-zero with a clear message if not, so
  a stopped instance yields a `failed` job whose log explains the fix.

**Job:** a single step → `d2-db-backup [-l <label>] <name>`. The script
`pg_dump | gzip`s to:

```
$DHIS2_BASE/_backups/<name>/<name>_<timestamp>_v<major>[_<label>].sql.gz
```

The existing filename pattern is preserved and the label is folded in (rather
than adopting the proposal's `<timestamp>[-<label>]` shape) so CLI- and
broker-created backups are named identically.

**Result on success** — same shape as a `GET /seeds` element, so the UI can show
it and offer it immediately as a restore source:

```json
{
  "path": "backups/<name>/<name>_20260614-091401_v42.sql.gz",
  "source": "backups",
  "size_bytes": 928374829,
  "modified": "2026-06-14T09:14:01+00:00"
}
```

The job worker computes this by scanning `_backups/<name>/` for the newest
`.sql.gz` by mtime. Because the worker is globally serialized, the newest file
after a successful backup job is always the one that job created — no log
parsing and no race. If no file is found (shouldn't happen on success),
`result` is `null`.

## 2. `POST /instances/<name>/upgrade`

Swap the running WAR (version bump or specific WAR), preserving the DB and
volumes. Covers both the "Deploy WAR…" and "upgrade" affordances. Returns
**202 + job**.

**Auth / scope:** the instance must be in scope for the token (agent tokens only
`agent-*`). `war_url`/`war_file` are admin only → `403` for agent tokens (same
rule as create).

**Body (one of `version` / `war_url` / `war_file` required):**

```json
{
  "version":  "2.42.4",
  "war_url":  "https://…/dhis.war",
  "war_file": "/abs/path.war",
  "tomcat":   "10",
  "backup_first": true
}
```

**Validation:**

- Exactly one WAR source required; none → `400`.
- `version` matches `^[0-9][0-9.]{0,15}$`. Major-only forms (`42`, `2.42`)
  resolve to latest stable at job-run time (reuses `d2-deploy-war -v`).
- `war_url` must start with `http://`/`https://`. `war_url`/`war_file` admin
  only.
- `tomcat` is `"9"` or `"10"`. The broker detects the instance's current Tomcat
  major from the container image (`docker inspect -f '{{.Config.Image}}'` →
  `tomcat:10-jdk17`, parsed by `tomcat:(\d+)`). If the requested value equals the
  current major → accept as a no-op. If it differs, or the current major can't
  be detected → `400 "changing the servlet container is not yet supported"`.
- `backup_first` defaults to `true`.

**Transition guard (`version` form only):** extract the target DHIS2 major from
the version string (`42` → 42, `2.42` → 42, `2.42.4` → 42) and read the current
major via `instance_version()`. Then:

- target major < current major → `400` (downgrade not supported).
- target major > current major + 1 → `400` (skipping majors not supported).
- same major or +1 → allowed.
- current major unreadable (DB down / fresh) → skip the guard; let Flyway
  decide.

`war_url`/`war_file` skip the guard entirely (the broker can't read the WAR's
version).

**Job steps (sequential; any failure fails the job and stops the rest):**

1. If `backup_first` (default) → `d2-db-backup <name>`. The printed backup path
   lands in the job log. A failed backup aborts the upgrade before any WAR swap,
   keeping a failed migration recoverable.
2. `d2-deploy-war [-v <version> | -l <war_url> | -f <war_file>] <name>` — stops
   Tomcat, swaps `webapps/ROOT.war`, starts Tomcat. DB and volumes untouched;
   Flyway runs migrations on boot.

**Result on success** — the `GET /instances` element for the instance (same as
`start`), plus best-effort `dhis2_major_version`:

```json
{
  "name": "demo1",
  "status": "running",
  "http_port": 9010,
  "pg_port": 5433,
  "localhost_url": "http://localhost:9010",
  "devnet_url": "http://dhis2-demo1:8080",
  "devnet_db": "dhis2-demo1-db:5432",
  "agent_managed": false,
  "dhis2_major_version": "42"
}
```

`dhis2_major_version` is best-effort and may still read the pre-upgrade major
because Flyway migrations run asynchronously on Tomcat boot, after the job
finishes. Clients should re-poll `GET /instances?full=1` once the instance is
back up to observe the migrated version.

## Code changes

### `bash-scripts-docker/d2-broker`

- **Routing** (`post_api`): add `backup` and `upgrade` actions next to
  `reset`/`start`/`stop`.
- **Handlers:** `backup_instance(name, scope)` and `upgrade_instance(name,
  scope)`, mirroring the structure of `reset_instance`/`start_stop`
  (read body → validate → `require_instance` → `check_no_active_job` → submit
  job).
- **`backup_instance`:** admin-only check; validate `label`; submit a job with
  `op="backup"`.
- **`upgrade_instance`:** validate WAR source / `tomcat` / transition guard;
  build the `d2-deploy-war` argv; optionally prepend a `d2-db-backup` step;
  submit a job with `op="upgrade"`.
- **`JobManager._worker` result computation:**
  - add `"upgrade"` to the branch that sets `result` to the matching
    `list_instances()` element, and attach `dhis2_major_version` (best-effort
    via `instance_version`) to that element.
  - add a `"backup"` branch: newest `.sql.gz` under `_backups/<name>/` →
    `GET /seeds`-shaped element (reuse the dump-element builder from
    `_scan_dump_files`).
- **Helpers:**
  - `extract_target_major(version)` — DHIS2 major from a version string.
  - `current_tomcat_major(name)` — parse the Tomcat container's image tag.
  - A small backup-element builder so the worker and `_scan_dump_files` share
    the same `path/source/size_bytes/modified` shape.

### `bash-scripts-docker/d2-db-backup`

Add `getopts` for an optional `-l <label>`, folded into the output filename.
Positional `<instance>` argument and all existing behaviour (DB-running check,
version-in-filename, restore hint) unchanged. CLI users without `-l` see no
difference.

### `docs/broker-api.md`

- New endpoint sections for `POST /instances/<name>/backup` and
  `POST /instances/<name>/upgrade`.
- Extend the `op` enum in the job examples (`create | reset | start | stop |
  delete | backup | upgrade`).
- Note the two new result shapes (backup → `GET /seeds` element; upgrade →
  `GET /instances` element with best-effort `dhis2_major_version`).
- Add `label`, `backup_first`, and the transition-guard rules to the validation
  cheat-sheet / scope notes.

## Testing

Manual verification against a live broker (no unit-test harness exists in this
repo; the broker is exercised end-to-end):

1. Create a `2.41` instance.
2. `POST /backup` (no label) → assert `202`, poll to `succeeded`, assert the
   `result` is a `backups/…` seeds element and the file exists on disk.
3. `POST /backup` with `label: "pre-upgrade"` → assert the label appears in the
   filename.
4. `POST /backup` with an agent token → `403`.
5. Stop the instance, `POST /backup` → job `failed` with the "start the instance
   first" message.
6. `POST /upgrade {version: "2.42"}` → assert `202`, a pre-upgrade backup line in
   the job log, WAR swapped, instance comes back up; later `?full=1` shows major
   `42`.
7. `POST /upgrade {version: "2.40"}` on the now-2.42 instance → `400` (downgrade).
8. `POST /upgrade {version: "2.45"}` → `400` (major skip).
9. `POST /upgrade {tomcat: "9"}` on a Tomcat-10 instance → `400` (servlet
   container change not supported).
10. `POST /upgrade` with no WAR source → `400`.
11. `POST /upgrade {war_url: …}` with an agent token → `403`.
