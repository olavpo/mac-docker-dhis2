# Disable all scheduled jobs on database restore

**Date:** 2026-07-20
**Status:** Approved

## Problem

Restored databases often contain enabled scheduled jobs — analytics runs,
predictors, monitoring, push analysis, and data/metadata sync jobs. On a local
laptop instance these can suddenly trigger heavy work, and sync jobs could even
push data to a remote server. Nothing in the restore flow prevents this today.

## Decision

Every database restore disables **all** jobs in the `jobconfiguration` table —
user-created and built-in housekeeping jobs alike. Unconditional: no opt-out
flag. Anyone who wants a job running re-enables it in the Scheduler app.

Alternatives considered and rejected:

- **Only user-created jobs** (keep built-in system jobs, identified by their
  fixed well-known UIDs): keeps housekeeping running, but the UID list must be
  maintained as DHIS2 versions add default jobs.
- **Only heavy job types** (`ANALYTICS_TABLE`, `PREDICTOR`, `*_SYNC`, …): most
  surgical, most brittle, and misses anything unexpected.
- **`--keep-jobs` opt-out flag:** more surface area (script + broker
  pass-through) for a rare case.

## Design

### Where

`d2-db-restore`, in the existing post-restore fixup window: after the restore
succeeds, next to the `d2-local-admin add` step, while Tomcat is still
stopped. Because `d2-instance-create --seed` and the broker's restore endpoint
both delegate to `d2-db-restore`, all three restore paths are covered by one
change.

### What

One guarded SQL statement against the restored DB via
`docker exec … psql`, inline in `d2-db-restore` (~8 lines, reusing the
already-resolved `$DB_CONTAINER`):

```sql
DO $$ BEGIN
  IF to_regclass('public.jobconfiguration') IS NOT NULL THEN
    UPDATE jobconfiguration SET enabled = false;
  END IF;
END $$;
```

- Disables everything, including jobs left queued as "run now"
  (`ONCE_ASAP`) in the dump.
- The `to_regclass` guard makes it a no-op on ancient dumps without the
  table, instead of an error.
- Runs before Tomcat restarts, so the scheduler never sees enabled jobs — no
  race window.
- Inline rather than a standalone script like `d2-local-admin`: a single
  UPDATE with no add/remove lifecycle doesn't warrant its own script.

### Error handling

Best-effort with a visible `⚠️` warning, same as the `local_admin` step — a
failed UPDATE after a successful multi-GB restore must not fail the whole
command. On success, print the number of jobs disabled (e.g. "Disabled 7
scheduled jobs") so the user can see it worked.

### Docs

One-line mention of the behavior in `README.md` and `docs/broker.md` where
restore behavior is described.

### Testing

Manual: restore a dump containing enabled jobs, verify
`SELECT count(*) FROM jobconfiguration WHERE enabled;` returns 0 and the
Scheduler app shows everything disabled. No automated harness exists for
`d2-db-restore`.
