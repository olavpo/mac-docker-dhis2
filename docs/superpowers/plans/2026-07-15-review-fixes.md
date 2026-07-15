# Project Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the correctness/robustness/polish findings from the 2026-07-14 project review (README doc-drift items were already fixed during the Doris work).

**Architecture:** Small, independent fixes across the bash scripts and the single-file Python broker. Broker changes get unit tests first (pure helpers extracted where the current code is impure); bash version helpers get a minimal test script; a GitHub Actions workflow wires shellcheck + both test suites.

**Tech Stack:** bash, Python 3 stdlib, GitHub Actions, shellcheck.

## Global Constraints

- Instance-name regex everywhere: `^[a-z][a-z0-9_-]{1,29}$` (must stay identical between broker `NAME_RE` and `d2-instance-create`).
- Broker stays a single stdlib-only Python file.
- All broker tests run without Docker: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`.
- Backup/seed filename version convention: `_vNN` (case-insensitive), e.g. `myinst_2026-01-01_v42.sql.gz`, `dhis2-db-sierra-leone_v42.sql.gz`.

---

### Task 1: Instance name validation in d2-instance-create

**Files:** Modify: `bash-scripts-docker/d2-instance-create` (right after `INSTANCE=$1`, ~line 138)

- [x] Add after `INSTANCE=$1`:

```bash
# Same rule the broker enforces (NAME_RE in d2-broker): sane compose project
# names, no path tricks, and no collision with reserved _-prefixed dirs.
if ! [[ "$INSTANCE" =~ ^[a-z][a-z0-9_-]{1,29}$ ]]; then
  echo "Error: instance name must match ^[a-z][a-z0-9_-]{1,29}\$ (got '$INSTANCE')" >&2
  exit 1
fi
```

- [x] Verify: `d2-instance-create "bad name"` and `_reserved` exit 1 with the message; no dirs created.
- [x] Fix `d2-db-version`'s generated temp name (`dbversion_$(date +%s)_$RANDOM` starts fine but check length ≤30 and no uppercase — adjust to fit the regex).
- [x] Commit.

### Task 2: set -u–safe DHIS2_BASE guards

**Files:** Modify: `bash-scripts-docker/d2-deploy-war:15`, `bash-scripts-docker/d2-db-restore:52`, `bash-scripts-docker/d2-info:13`

- [x] Change `[ -z "$DHIS2_BASE" ]` to `[ -z "${DHIS2_BASE:-}" ]` in all three.
- [x] Verify: `env -u DHIS2_BASE d2-info` prints the friendly error, not "unbound variable".
- [x] Commit.

### Task 3: Broker — atomic active-job check + submit

**Files:** Modify: `bash-scripts-docker/d2-broker` (JobManager.submit, check_no_active_job); Test: `bash-scripts-docker/test_d2_broker.py`

**Interfaces:** `JobManager.submit(...)` raises `ApiError(409, ...)` itself when the instance already has a queued/running job, under one `self.lock` hold. Handler-level `check_no_active_job` pre-checks stay as fast-fail (non-authoritative).

- [x] Write failing test `SubmitExclusive`: build a `JobManager` pointed at a temp `DHIS2_BASE` (env var + `tempfile`), stub the worker (don't consume queue: pre-set a fake queued job in `self.jobs`), assert second `submit` for same instance raises `ApiError` with status 409, different instance succeeds.
- [x] Implement: move the active-check inside `submit` under `self.lock` (check `queued`/`running` jobs for `instance`, raise `ApiError(409, ...)`, else insert). Keep `check_no_active_job` delegating to the same logic for early 409s.
- [x] Tests pass. Commit.

### Task 4: Broker — reset seed version guard

**Files:** Modify: `bash-scripts-docker/d2-broker` (new pure helpers + `reset_instance`); Test: `bash-scripts-docker/test_d2_broker.py`; Docs: `docs/broker.md`, `docs/broker-api.md`

**Interfaces:**
- `seed_major(filename: str) -> int | None` — extracts `NN` from a `_vNN` / `_VNN` token in the basename.
- `reset_error(seed_maj, cur_maj) -> str | None` — message iff `seed_maj > cur_maj` (a newer DB than the running WAR bricks Flyway); `None` if either side unknown.

- [x] Write failing tests: `SeedMajor` (`..._v42.sql.gz` → 42, `..._V40.sql.gz` → 40, no token → None) and `ResetError` (seed 42 into 41 → message; 41 into 42 → None; unknowns → None).
- [x] Implement helpers; wire into `reset_instance` after `require_instance` (use `instance_version(name)`; both sides best-effort).
- [x] Tests pass. Document the new 400 in `docs/broker.md` + `docs/broker-api.md`. Commit.

### Task 5: Broker — IPv6-safe port parsing (+ d2-info)

**Files:** Modify: `bash-scripts-docker/d2-broker` (`container_port` → extract `parse_host_port`), `bash-scripts-docker/d2-info:46,52`; Test: `bash-scripts-docker/test_d2_broker.py`

**Interfaces:** `parse_host_port(docker_port_output: str) -> int | None` — first host port from any address family.

- [x] Failing tests: `ParseHostPort` — `"0.0.0.0:9010\n[::]:9010"` → 9010, IPv6-only `"[::]:9012"` → 9012, `""` → None.
- [x] Implement (`re.search(r":(\d+)\s*$", line)` per line); `container_port` delegates. Fix d2-info's `grep -m1 '0\.0\.0\.0:'` to take the first line and strip up to the last colon.
- [x] Tests pass. Commit.

### Task 6: Broker — prune old job records on startup

**Files:** Modify: `bash-scripts-docker/d2-broker` (`JobManager._load_previous`); Test: `bash-scripts-docker/test_d2_broker.py`

**Interfaces:** `jobs_to_prune(jobs: list[dict], keep: int = 200) -> list[str]` — ids of terminal jobs beyond the newest `keep` (by `created_at`); active jobs never pruned.

- [x] Failing tests: newest `keep` survive, older terminal ids returned, queued/running never returned.
- [x] Implement; `_load_previous` deletes pruned `.json` + `.log` files and skips loading them.
- [x] Tests pass. Commit.

### Task 7: d2-db-restore — stream dumps, kill server-side on timeout

**Files:** Modify: `bash-scripts-docker/d2-db-restore`

- [x] Replace the temp-file + `docker cp` path for `.sql` / `.sql.gz` with streaming: `gunzip -c` / `cat` piped to `docker exec -i "$DB_CONTAINER" psql -U dhis -d dhis2`. Keep `docker cp` only for `.pgc` (parallel `pg_restore -j 4` needs a seekable file).
- [x] On timeout, after killing the local client, also `pg_terminate_backend` all `dhis2` connections so the server-side restore actually stops.
- [x] Verify with a real small dump (restore into a scratch instance, gz and plain).
- [x] Commit.

### Task 8: d2-db-version — trap cleanup

**Files:** Modify: `bash-scripts-docker/d2-db-version`

- [x] `CREATED=0` before create; set to 1 after; `trap 'cleanup' EXIT` where cleanup deletes the temp instance if `CREATED=1`. Remove the per-error manual delete calls.
- [x] Verify: Ctrl-C mid-run leaves no instance dir behind.
- [x] Commit.

### Task 9: d2-deploy-war — real port in the hint

**Files:** Modify: `bash-scripts-docker/d2-deploy-war`

- [x] Drop the `-p` flag; after `docker start`, read the mapped port via `docker port "$CONTAINER" 8080/tcp` (fall back to "8080" text if unmapped).
- [x] Verify hint shows the instance's actual port. Commit.

### Task 10: Bash tests + CI

**Files:** Create: `bash-scripts-docker/test_d2_lib.sh`. ~~`.github/workflows/ci.yml`~~ (CI workflow skipped per user, 2026-07-15)

- [x] `test_d2_lib.sh`: assert-based tests for the offline paths of `normalize_version` (`41.2`→`2.41.2`, `2.42.4`→`2.42.4`), `dhis2_major` (`2.42.4`→42, `42`→42, `41.4`→41), `required_tomcat_for_major` (41→9, 42→10, junk→10), plus the name regex used in Task 1.
- [x] ~~CI workflow~~ Skipped per user. Local equivalents: `shellcheck --exclude=SC1091` on the bash scripts, `python3 -m unittest test_d2_broker`, `bash test_d2_lib.sh`.
- [x] Fix/annotate anything shellcheck flags until clean.
- [x] Commit.
