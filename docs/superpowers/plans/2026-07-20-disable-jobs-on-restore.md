# Disable All Scheduled Jobs on Database Restore — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every database restore disables all rows in the restored DB's `jobconfiguration` table, so no scheduled job (analytics, predictor, sync, …) suddenly triggers on a local instance.

**Architecture:** One inline step in `bash-scripts-docker/d2-db-restore`, in the existing post-restore fixup window (after the restore succeeds, next to the `d2-local-admin add` step, while Tomcat is still stopped). `d2-instance-create --seed` and the broker's restore endpoint both delegate to `d2-db-restore`, so all three restore paths are covered by this one change. Spec: `docs/superpowers/specs/2026-07-20-disable-jobs-on-restore-design.md`.

**Tech Stack:** Bash, `docker exec … psql` against the instance's Postgres container.

## Global Constraints

- Disable **all** jobs — user-created and built-in housekeeping alike. No opt-out flag.
- Best-effort: a failure here prints a `⚠️` warning but must NOT fail the restore (same policy as the `local_admin` step).
- Must be a no-op (not an error) on dumps without a `jobconfiguration` table.
- Must run before Tomcat is restarted.
- On success, print the number of jobs disabled.
- Scripts must stay `shellcheck`-clean (repo convention, see commit ca05d44).
- There is no automated test harness for `d2-db-restore`; verification is `bash -n` + `shellcheck` + a manual live-restore check.

---

### Task 1: Disable-jobs step in d2-db-restore

**Files:**
- Modify: `bash-scripts-docker/d2-db-restore` (insert between the `local_admin` block ending at line 180 and the `# Restart Tomcat container` comment at line 182)

**Interfaces:**
- Consumes: `$DB_CONTAINER` (already resolved at `d2-db-restore:88`), running Postgres container with database `dhis2` and role `dhis`.
- Produces: shell function `disable_scheduled_jobs` (no args, returns non-zero on psql failure) — used only within this script; no later task depends on it.

**Note on SQL shape vs spec:** the spec illustrates a `DO $$ … $$` block, but `DO` cannot return the row count the spec also requires. Same guard, same UPDATE, expressed as two statements instead: a `to_regclass` existence check, then an `UPDATE … RETURNING` wrapped in a CTE to get the count. Semantics are identical.

- [ ] **Step 1: Insert the function and call site**

In `bash-scripts-docker/d2-db-restore`, directly after the `local_admin` block:

```bash
# Ensure the known local_admin superuser exists regardless of what was
# restored. Tomcat is still stopped here, so the restart below rebuilds the
# user cache. Best-effort: the restore itself already succeeded.
echo "Ensuring local_admin superuser..."
if ! "$SCRIPT_DIR/d2-local-admin" "$INSTANCE" add; then
  echo "⚠️  Warning: could not ensure local_admin (restore itself succeeded)"
fi
```

and before:

```bash
# Restart Tomcat container
echo "Restarting Tomcat container..."
```

insert:

```bash
# Disable ALL scheduled jobs in the restored DB (analytics, predictors, sync
# jobs, housekeeping) so nothing suddenly triggers on a local instance. Tomcat
# is still stopped, so the scheduler never sees them enabled. Best-effort,
# like local_admin above.
# See docs/superpowers/specs/2026-07-20-disable-jobs-on-restore-design.md
disable_scheduled_jobs() {
  local has_table count
  has_table=$(docker exec "$DB_CONTAINER" psql -U dhis -d dhis2 -tA -v ON_ERROR_STOP=1 \
    -c "SELECT to_regclass('public.jobconfiguration') IS NOT NULL;") || return 1
  if [ "$has_table" != "t" ]; then
    echo "No jobconfiguration table in this database; nothing to disable"
    return 0
  fi
  count=$(docker exec "$DB_CONTAINER" psql -U dhis -d dhis2 -tA -v ON_ERROR_STOP=1 \
    -c "WITH updated AS (UPDATE jobconfiguration SET enabled = false WHERE enabled RETURNING 1) SELECT count(*) FROM updated;") || return 1
  echo "Disabled $count scheduled job(s)"
}

echo "Disabling scheduled jobs..."
if ! disable_scheduled_jobs; then
  echo "⚠️  Warning: could not disable scheduled jobs (restore itself succeeded)"
fi
```

- [ ] **Step 2: Syntax and lint check**

Run:

```bash
bash -n bash-scripts-docker/d2-db-restore && shellcheck bash-scripts-docker/d2-db-restore
```

Expected: no output, exit 0 from both.

- [ ] **Step 3: Manual live verification (requires a local instance + a seed/backup dump)**

Skip this step only if no instance/dump is available on the machine, and say so in the task report.

```bash
# pick any existing instance and any .sql.gz dump, e.g. from $DHIS2_BASE/_seeds/
bash-scripts-docker/d2-db-restore <instance> <dump.sql.gz>
```

Expected in the output, after "Ensuring local_admin superuser...":

```
Disabling scheduled jobs...
Disabled N scheduled job(s)
```

(N ≥ 0; stock demo DBs have several enabled jobs, so usually N > 0.) Then confirm nothing is left enabled:

```bash
bash-scripts-docker/d2-psql <instance> -c "SELECT count(*) FROM jobconfiguration WHERE enabled;"
```

Expected: `0`.

- [ ] **Step 4: Commit**

```bash
git add bash-scripts-docker/d2-db-restore
git commit -m "d2-db-restore: disable all scheduled jobs after restore

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Document the behavior

**Files:**
- Modify: `README.md:73` (script list bullet) and `README.md` "Backup and restore database" section (restore example, around line 140)
- Modify: `docs/broker.md` (paragraph about `local_admin` on restore, around lines 144–146)

**Interfaces:**
- Consumes: behavior implemented in Task 1 (all jobs disabled on every restore, count printed).
- Produces: nothing — docs only.

- [ ] **Step 1: Update README script-list bullet**

In `README.md`, change:

```markdown
  - `d2-db-restore` — restore DB from `.sql`, `.sql.gz`, or `.pgc`
```

to:

```markdown
  - `d2-db-restore` — restore DB from `.sql`, `.sql.gz`, or `.pgc`; disables all scheduled jobs in the restored DB
```

- [ ] **Step 2: Update README restore section**

In `README.md`, change:

```markdown
Restore:

```bash
d2-db-restore myinstance $DHIS2_BASE/_backups/myinstance/myinstance_20240101-120000_v41.sql.gz
```
```

to:

```markdown
Restore:

```bash
d2-db-restore myinstance $DHIS2_BASE/_backups/myinstance/myinstance_20240101-120000_v41.sql.gz
```

Every restore disables **all** scheduled jobs (`jobconfiguration.enabled = false`)
in the restored database — analytics runs, predictors, sync jobs and built-in
housekeeping alike — so nothing suddenly triggers on a local instance.
Re-enable individual jobs in the Scheduler app if you need them.
```

- [ ] **Step 3: Update docs/broker.md**

In `docs/broker.md`, change:

```markdown
A known `local_admin` / `district` superuser (`ALL` authority) is ensured on
every restore and create, independent of the restored database's own `admin`,
and is stripped from backup dumps.
```

to:

```markdown
A known `local_admin` / `district` superuser (`ALL` authority) is ensured on
every restore and create, independent of the restored database's own `admin`,
and is stripped from backup dumps. Every restore also disables all scheduled
jobs in the restored database, so restored seeds never fire analytics,
predictor or sync jobs on their own.
```

- [ ] **Step 4: Commit**

```bash
git add README.md docs/broker.md
git commit -m "docs: note that restores disable all scheduled jobs

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
