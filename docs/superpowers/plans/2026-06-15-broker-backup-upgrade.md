# d2-broker backup + upgrade endpoints — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `POST /instances/<name>/backup` and `POST /instances/<name>/upgrade` to `d2-broker`, following the existing 202+job model.

**Architecture:** Both endpoints wrap existing scripts (`d2-db-backup`, `d2-deploy-war`) as jobs on the single global worker. New pure helpers handle version-major math, version-transition guards, and Tomcat-image parsing; these are unit-tested. The Docker/HTTP-touching handlers are verified end-to-end against a live broker. One backwards-compatible change adds a `-l <label>` flag to `d2-db-backup`.

**Tech Stack:** Python 3 (stdlib only, `unittest`), Bash, Docker.

**Spec:** `docs/superpowers/specs/2026-06-15-broker-backup-upgrade-design.md`

---

## File structure

- `bash-scripts-docker/d2-broker` — modify: add helpers, two handlers, routing, worker result branches.
- `bash-scripts-docker/d2-db-backup` — modify: add `-l <label>` flag.
- `bash-scripts-docker/test_d2_broker.py` — create: `unittest` tests for the pure helpers (loads `d2-broker` via `importlib`, no Docker needed).
- `docs/broker-api.md` — modify: document the two endpoints.

The `d2-broker` script has no `.py` extension, so the test loads it by file path with `importlib.util`. Importing has no side effects (module-level code only defines functions/constants; `main()` runs under `if __name__ == "__main__"`).

---

## Task 1: Test harness + `extract_target_major`

Pure helper: DHIS2 major version from a version string (`"42"`/`"2.42"`/`"2.42.4"` → `42`).

**Files:**
- Create: `bash-scripts-docker/test_d2_broker.py`
- Modify: `bash-scripts-docker/d2-broker` (add `extract_target_major` near the other helpers, after `VERSION_RE` ~line 60 or alongside `instance_version`)

- [ ] **Step 1: Write the failing test**

Create `bash-scripts-docker/test_d2_broker.py`:

```python
"""Unit tests for the pure helpers in d2-broker (no Docker required).

Loads the extension-less d2-broker script as a module via importlib.
Run: python3 -m unittest test_d2_broker -v   (from bash-scripts-docker/)
"""
import importlib.util
import os
import unittest
from datetime import timezone
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "d2broker", os.path.join(_HERE, "d2-broker"))
broker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(broker)


class ExtractTargetMajor(unittest.TestCase):
    def test_bare_major(self):
        self.assertEqual(broker.extract_target_major("42"), 42)

    def test_two_dot_major(self):
        self.assertEqual(broker.extract_target_major("2.42"), 42)

    def test_full_version(self):
        self.assertEqual(broker.extract_target_major("2.42.4"), 42)

    def test_non_numeric_returns_none(self):
        self.assertIsNone(broker.extract_target_major(""))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: FAIL — `AttributeError: module 'd2broker' has no attribute 'extract_target_major'`

- [ ] **Step 3: Write minimal implementation**

In `bash-scripts-docker/d2-broker`, add after the `instance_version` function (~line 213):

```python
def extract_target_major(version):
    """DHIS2 major from a version string: '42'/'2.42'/'2.42.4' -> 42.
    Returns None if the major is not numeric."""
    parts = str(version).split(".")
    major = parts[1] if parts[0] == "2" and len(parts) >= 2 else parts[0]
    try:
        return int(major)
    except ValueError:
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add bash-scripts-docker/test_d2_broker.py bash-scripts-docker/d2-broker
git commit -m "feat(broker): extract_target_major helper + test harness"
```

---

## Task 2: `transition_error` guard

Pure helper: returns an error message for an unsupported version transition, else `None`.

**Files:**
- Modify: `bash-scripts-docker/d2-broker` (add after `extract_target_major`)
- Modify: `bash-scripts-docker/test_d2_broker.py`

- [ ] **Step 1: Write the failing test**

Add to `test_d2_broker.py`:

```python
class TransitionError(unittest.TestCase):
    def test_same_major_ok(self):
        self.assertIsNone(broker.transition_error(42, 42))

    def test_one_up_ok(self):
        self.assertIsNone(broker.transition_error(41, 42))

    def test_downgrade_rejected(self):
        msg = broker.transition_error(42, 41)
        self.assertIsNotNone(msg)
        self.assertIn("downgrade", msg)

    def test_major_skip_rejected(self):
        msg = broker.transition_error(42, 44)
        self.assertIsNotNone(msg)
        self.assertIn("skipping", msg)

    def test_unknown_current_skips_guard(self):
        self.assertIsNone(broker.transition_error(None, 42))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: FAIL — `AttributeError: module 'd2broker' has no attribute 'transition_error'`

- [ ] **Step 3: Write minimal implementation**

In `d2-broker`, after `extract_target_major`:

```python
def transition_error(current_major, target_major):
    """Message for an unsupported DHIS2 version transition, else None.
    Only enforced when both majors are known; downgrades and major-skips
    are rejected, same-major and +1 are allowed."""
    if current_major is None or target_major is None:
        return None
    if target_major < current_major:
        return (f"downgrade not supported: instance is on DHIS2 "
                f"{current_major}, requested {target_major}")
    if target_major > current_major + 1:
        return (f"skipping major versions not supported: instance is on "
                f"DHIS2 {current_major}, requested {target_major} "
                f"(upgrade one major at a time)")
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add bash-scripts-docker/d2-broker bash-scripts-docker/test_d2_broker.py
git commit -m "feat(broker): transition_error version guard"
```

---

## Task 3: `parse_tomcat_major` + `current_tomcat_major`

Pure parser for a Tomcat image tag, plus a thin Docker wrapper that uses it.

**Files:**
- Modify: `bash-scripts-docker/d2-broker` (add after `transition_error`)
- Modify: `bash-scripts-docker/test_d2_broker.py`

- [ ] **Step 1: Write the failing test**

Add to `test_d2_broker.py`:

```python
class ParseTomcatMajor(unittest.TestCase):
    def test_tomcat_10(self):
        self.assertEqual(broker.parse_tomcat_major("tomcat:10-jdk17"), 10)

    def test_tomcat_9(self):
        self.assertEqual(broker.parse_tomcat_major("tomcat:9.0-jdk17"), 9)

    def test_empty(self):
        self.assertIsNone(broker.parse_tomcat_major(""))

    def test_no_match(self):
        self.assertIsNone(broker.parse_tomcat_major("postgis:16-3.5"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: FAIL — `AttributeError: module 'd2broker' has no attribute 'parse_tomcat_major'`

- [ ] **Step 3: Write minimal implementation**

In `d2-broker`, after `transition_error`:

```python
def parse_tomcat_major(image):
    """Tomcat major from a container image tag, e.g.
    'tomcat:10-jdk17' -> 10, 'tomcat:9.0-jdk17' -> 9. None if not found."""
    m = re.search(r"tomcat:(\d+)", image or "")
    return int(m.group(1)) if m else None


def current_tomcat_major(name):
    """The Tomcat major of the instance's running/created container, or
    None if it can't be determined."""
    for c in (f"{name}-tomcat-1", f"{name}_tomcat_1"):
        out = run_out(["docker", "inspect", "-f",
                       "{{.Config.Image}}", c]).strip()
        major = parse_tomcat_major(out)
        if major is not None:
            return major
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: PASS (13 tests). `current_tomcat_major` is covered by the live verification in Task 8 (it touches Docker).

- [ ] **Step 5: Commit**

```bash
git add bash-scripts-docker/d2-broker bash-scripts-docker/test_d2_broker.py
git commit -m "feat(broker): tomcat image-tag parsing helpers"
```

---

## Task 4: `dump_element` (DRY the seeds-element builder)

Extract the `path/source/size_bytes/modified` builder so `_scan_dump_files` and the backup worker branch share it.

**Files:**
- Modify: `bash-scripts-docker/d2-broker:215-229` (`_scan_dump_files`)
- Modify: `bash-scripts-docker/test_d2_broker.py`

- [ ] **Step 1: Write the failing test**

Add to `test_d2_broker.py` (uses a temp dir, no Docker):

```python
import tempfile


class DumpElement(unittest.TestCase):
    def test_backups_path_prefixed_and_relative(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = root / "agent-x" / "agent-x_20260614-091401_v42.sql.gz"
            f.parent.mkdir(parents=True)
            f.write_bytes(b"hello")
            elem = broker.dump_element(f, "backups", root)
            self.assertEqual(
                elem["path"],
                "backups/agent-x/agent-x_20260614-091401_v42.sql.gz")
            self.assertEqual(elem["source"], "backups")
            self.assertEqual(elem["size_bytes"], 5)
            self.assertTrue(elem["modified"].endswith("+00:00"))

    def test_seeds_path_not_prefixed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = root / "demo.sql.gz"
            f.write_bytes(b"x")
            elem = broker.dump_element(f, "seeds", root)
            self.assertEqual(elem["path"], "demo.sql.gz")
            self.assertEqual(elem["source"], "seeds")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: FAIL — `AttributeError: module 'd2broker' has no attribute 'dump_element'`

- [ ] **Step 3: Write minimal implementation**

In `d2-broker`, replace `_scan_dump_files` (lines 215-229) with:

```python
def dump_element(path, source, root):
    """Build a GET /seeds-shaped element for a dump file under `root`.
    Backup sources get a 'backups/' path prefix."""
    prefix = "backups/" if source == "backups" else ""
    st = path.stat()
    return {
        "path": prefix + str(path.relative_to(root)),
        "source": source,
        "size_bytes": st.st_size,
        "modified": datetime.fromtimestamp(
            st.st_mtime, timezone.utc).isoformat(timespec="seconds"),
    }


def _scan_dump_files(root, source):
    if not root.is_dir():
        return []
    return [dump_element(p, source, root)
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name.endswith(SEED_SUFFIXES)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add bash-scripts-docker/d2-broker bash-scripts-docker/test_d2_broker.py
git commit -m "refactor(broker): extract dump_element shared by seeds + backups"
```

---

## Task 5: `d2-db-backup` — `-l <label>` flag

Backwards-compatible label flag folded into the backup filename.

**Files:**
- Modify: `bash-scripts-docker/d2-db-backup:17-27` (usage + arg parsing) and `:58-64` (filename)

- [ ] **Step 1: Add option parsing and update usage**

Replace lines 17-27 (the `usage()` function and the `if [ $# -eq 0 ]` / `INSTANCE=$1` block) with:

```bash
usage() {
  echo "Usage: $0 [-l <label>] <instance name>"
  echo "Creates a timestamped database backup in the common [Backups] directory"
  echo "  -l <label>   optional tag folded into the backup filename"
  exit 1
}

LABEL=""
while getopts "l:" opt; do
  case $opt in
    l) LABEL=$OPTARG ;;
    *) usage ;;
  esac
done
shift $((OPTIND-1))

if [ $# -eq 0 ]; then
  usage
fi

INSTANCE=$1
```

- [ ] **Step 2: Fold the label into the filename**

Replace lines 58-64 (the `TIMESTAMP=` / `BACKUP_FILE=` block) with:

```bash
# Create backup with timestamp, version, and optional label
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
SUFFIX=""
if [ -n "$LABEL" ]; then
  SUFFIX="_${LABEL}"
fi
if [ -n "$MAJOR_VERSION" ]; then
  BACKUP_FILE="$BACKUP_DIR/${INSTANCE}_${TIMESTAMP}_v${MAJOR_VERSION}${SUFFIX}.sql.gz"
else
  BACKUP_FILE="$BACKUP_DIR/${INSTANCE}_${TIMESTAMP}${SUFFIX}.sql.gz"
fi
```

- [ ] **Step 3: Syntax-check the script**

Run: `bash -n bash-scripts-docker/d2-db-backup`
Expected: no output, exit 0. (Filename behaviour is verified end-to-end in Task 8.)

- [ ] **Step 4: Commit**

```bash
git add bash-scripts-docker/d2-db-backup
git commit -m "feat(d2-db-backup): optional -l label flag"
```

---

## Task 6: backup endpoint — handler + routing + worker result

**Files:**
- Modify: `bash-scripts-docker/d2-broker` — `post_api` (~line 575), new `backup_instance` method, `JobManager._worker` result block (~line 399-404)

- [ ] **Step 1: Route the action**

In `post_api`, inside the `if len(parts) == 3 and parts[0] == "instances":` block, after the `start`/`stop` line (~line 584), add:

```python
            if action == "backup":
                return self.backup_instance(name, scope)
```

- [ ] **Step 2: Add the handler**

Add a `backup_instance` method to `Handler`, after `reset_instance` (~line 679):

```python
    def backup_instance(self, name, scope):
        if scope != "all":
            raise ApiError(403, "backups require the admin token")
        body = self.read_body()
        label = body.get("label")
        self.require_instance(name)
        self.check_no_active_job(name)
        argv = [str(SCRIPT_DIR / "d2-db-backup")]
        if label is not None:
            if not re.match(r"^[a-z0-9][a-z0-9_-]{0,39}$", str(label)):
                raise ApiError(400, "label must match "
                                    "^[a-z0-9][a-z0-9_-]{0,39}$")
            argv += ["-l", str(label)]
        argv.append(name)
        return self.job_response(self.jobs.submit("backup", name, [argv]))
```

- [ ] **Step 3: Compute the backup job result**

In `JobManager._worker`, replace the result block (lines 399-404, the `if job["exit_code"] == 0:` body that sets `result` and `final`) with:

```python
                if job["exit_code"] == 0:
                    if job["op"] in ("create", "reset", "start", "upgrade"):
                        elem = next(
                            (i for i in list_instances()
                             if i["name"] == job["instance"]), None)
                        if elem and job["op"] == "upgrade":
                            elem["dhis2_major_version"] = instance_version(
                                job["instance"])
                        job["result"] = elem
                    elif job["op"] == "backup":
                        root = backups_dir() / job["instance"]
                        dumps = sorted(
                            (p for p in root.rglob("*")
                             if p.is_file()
                             and p.name.endswith(SEED_SUFFIXES)),
                            key=lambda p: p.stat().st_mtime)
                        job["result"] = (
                            dump_element(dumps[-1], "backups", backups_dir())
                            if dumps else None)
                    final = "succeeded"
                else:
```

(This block also adds `"upgrade"` for Task 7; the `upgrade` route itself is added there.)

- [ ] **Step 4: Verify the module still imports and tests pass**

Run: `cd bash-scripts-docker && python3 -c "import importlib.util,os; s=importlib.util.spec_from_file_location('b','d2-broker'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('ok')" && python3 -m unittest test_d2_broker -v`
Expected: prints `ok`, then PASS (15 tests). Endpoint behaviour is verified in Task 8.

- [ ] **Step 5: Commit**

```bash
git add bash-scripts-docker/d2-broker
git commit -m "feat(broker): POST /instances/<name>/backup endpoint"
```

---

## Task 7: upgrade endpoint — handler + routing

The worker result branch for `upgrade` was already added in Task 6, Step 3.

**Files:**
- Modify: `bash-scripts-docker/d2-broker` — `post_api`, new `upgrade_instance` method

- [ ] **Step 1: Route the action**

In `post_api`, after the `backup` route added in Task 6, add:

```python
            if action == "upgrade":
                return self.upgrade_instance(name, scope)
```

- [ ] **Step 2: Add the handler**

Add an `upgrade_instance` method to `Handler`, after `backup_instance`:

```python
    def upgrade_instance(self, name, scope):
        body = self.read_body()
        self.require_instance(name)
        self.check_no_active_job(name)
        version = body.get("version")
        war_url = body.get("war_url")
        war_file = body.get("war_file")
        provided = [s for s in (version, war_url, war_file) if s]
        if len(provided) != 1:
            raise ApiError(400, "upgrade requires exactly one of: "
                                "version, war_url, war_file")

        deploy = [str(SCRIPT_DIR / "d2-deploy-war")]
        if version:
            if not VERSION_RE.match(str(version)):
                raise ApiError(400, "version must look like 42, 2.42 or 2.42.1")
            cur = instance_version(name)
            cur_major = int(cur) if cur and cur.isdigit() else None
            err = transition_error(cur_major, extract_target_major(version))
            if err:
                raise ApiError(400, err)
            deploy += ["-v", str(version)]
        elif war_url:
            if scope != "all":
                raise ApiError(403, "war_url/war_file require the admin token")
            if not str(war_url).startswith(("http://", "https://")):
                raise ApiError(400, "war_url must be http(s)")
            deploy += ["-l", str(war_url)]
        else:  # war_file
            if scope != "all":
                raise ApiError(403, "war_url/war_file require the admin token")
            deploy += ["-f", str(war_file)]

        tomcat = body.get("tomcat")
        if tomcat is not None:
            if str(tomcat) not in ("9", "10"):
                raise ApiError(400, "tomcat must be 9 or 10")
            if current_tomcat_major(name) != int(tomcat):
                raise ApiError(400, "changing the servlet container is not "
                                    "yet supported")
        deploy.append(name)

        steps = []
        if body.get("backup_first", True):
            steps.append([str(SCRIPT_DIR / "d2-db-backup"), name])
        steps.append(deploy)
        return self.job_response(self.jobs.submit("upgrade", name, steps))
```

- [ ] **Step 3: Verify the module imports and tests pass**

Run: `cd bash-scripts-docker && python3 -c "import importlib.util; s=importlib.util.spec_from_file_location('b','d2-broker'); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('ok')" && python3 -m unittest test_d2_broker -v`
Expected: prints `ok`, then PASS (15 tests).

- [ ] **Step 4: Commit**

```bash
git add bash-scripts-docker/d2-broker
git commit -m "feat(broker): POST /instances/<name>/upgrade endpoint"
```

---

## Task 8: Live end-to-end verification

No automated harness exists for the Docker/HTTP paths; verify against a running broker. Requires Docker and `DHIS2_BASE` set.

**Files:** none (verification only).

- [ ] **Step 1: Start the broker and set up shell vars**

```bash
cd bash-scripts-docker
DHIS2_BASE="$DHIS2_BASE" ./d2-broker run &   # or use the installed launchd agent
TOKEN=$(python3 -c 'import json,os;print(json.load(open(os.environ["DHIS2_BASE"]+"/_broker/tokens.json"))["admin"]["token"])')
AGENT=$(python3 -c 'import json,os;print(json.load(open(os.environ["DHIS2_BASE"]+"/_broker/tokens.json"))["agent"]["token"])')
B=http://localhost:9300
H="Authorization: Bearer $TOKEN"
AH="Authorization: Bearer $AGENT"
```

Helper to wait for a job to reach a terminal state:

```bash
wait_job() { while true; do s=$(curl -s -H "$H" "$B/jobs/$1"|python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])'); echo "$s"; case "$s" in succeeded|failed|interrupted) break;; esac; sleep 2; done; }
```

- [ ] **Step 2: Create a 2.41 instance to work with**

```bash
curl -s -X POST -H "$H" -d '{"name":"agent-upg","version":"2.41"}' "$B/instances"
# grab job id from output, then: wait_job <id>
```
Expected: job `succeeded`; `GET /instances?full=1` shows `agent-upg` with `dhis2_major_version: "41"`.

- [ ] **Step 3: Backup, no label**

```bash
curl -s -X POST -H "$H" -d '{}' "$B/instances/agent-upg/backup"   # -> job id
# wait_job <id>; then inspect result:
curl -s -H "$H" "$B/jobs/<id>" | python3 -m json.tool
```
Expected: `result.path` is `backups/agent-upg/agent-upg_<ts>_v41.sql.gz`, `source: "backups"`, plausible `size_bytes`. File exists: `ls "$DHIS2_BASE/_backups/agent-upg/"`.

- [ ] **Step 4: Backup with a label**

```bash
curl -s -X POST -H "$H" -d '{"label":"pre-upgrade"}' "$B/instances/agent-upg/backup"
```
Expected: after success, filename contains `_pre-upgrade.sql.gz`.

- [ ] **Step 5: Backup rejections**

```bash
curl -s -X POST -H "$AH" -d '{}' "$B/instances/agent-upg/backup"            # -> 403
curl -s -X POST -H "$H" -d '{"label":"Bad Label!"}' "$B/instances/agent-upg/backup"  # -> 400
```
Expected: `403` (agent token), `400` (label regex).

- [ ] **Step 6: Backup of a stopped instance**

```bash
curl -s -X POST -H "$H" "$B/instances/agent-upg/stop"   # wait_job
curl -s -X POST -H "$H" -d '{}' "$B/instances/agent-upg/backup"  # wait_job
```
Expected: job `failed`; `/jobs/<id>/log` contains the "is not running … Start the instance first" message. Then restart: `curl -s -X POST -H "$H" "$B/instances/agent-upg/start"` and `wait_job`.

- [ ] **Step 7: Upgrade 2.41 → 2.42 (with default backup_first)**

```bash
curl -s -X POST -H "$H" -d '{"version":"2.42"}' "$B/instances/agent-upg/upgrade"  # wait_job
```
Expected: job `succeeded`; `/jobs/<id>/log` shows a `d2-db-backup` step then `d2-deploy-war`; `result` is the instance element. After the instance finishes booting, `GET /instances?full=1` eventually reports `dhis2_major_version: "42"`.

- [ ] **Step 8: Upgrade rejections**

```bash
curl -s -X POST -H "$H" -d '{"version":"2.41"}' "$B/instances/agent-upg/upgrade"   # 400 downgrade
curl -s -X POST -H "$H" -d '{"version":"2.45"}' "$B/instances/agent-upg/upgrade"   # 400 major skip
curl -s -X POST -H "$H" -d '{"tomcat":"9"}'     "$B/instances/agent-upg/upgrade"   # 400 no source + ... (see note)
curl -s -X POST -H "$H" -d '{}'                 "$B/instances/agent-upg/upgrade"   # 400 no source
curl -s -X POST -H "$H" -d '{"version":"2.42","tomcat":"9"}' "$B/instances/agent-upg/upgrade"  # 400 servlet container
curl -s -X POST -H "$AH" -d '{"war_url":"https://x/d.war"}' "$B/instances/agent-upg/upgrade"   # 403
```
Expected status codes as annotated. Note: `{"tomcat":"9"}` alone fails the "exactly one source" check (`400`) before the tomcat check — that is correct; the dedicated servlet-container `400` is exercised by the `{"version":"2.42","tomcat":"9"}` call.

- [ ] **Step 9: Clean up**

```bash
curl -s -X DELETE -H "$H" "$B/instances/agent-upg"   # wait_job
```

- [ ] **Step 10: Commit (if any fixes were needed)**

Only if Steps 1-9 surfaced bugs requiring code changes; otherwise nothing to commit.

---

## Task 9: Update `docs/broker-api.md`

**Files:**
- Modify: `docs/broker-api.md`

- [ ] **Step 1: Add the two endpoint sections**

After the `### DELETE /instances/<name>` section (~line 228), add:

````markdown
### `POST /instances/<name>/backup`

Create a `pg_dump` backup of the instance's database. **Admin only** (agent
token → `403`). Returns **202 + job**.

Body (all optional):

```json
{ "label": "pre-upgrade" }   // folded into the backup filename
```

- `label` matches `^[a-z0-9][a-z0-9_-]{0,39}$`.
- The DB must be running; on a stopped instance the job `fail`s with a
  "start the instance first" message.

On success, `result` is a `GET /seeds`-shaped element for the new backup, so
it can be offered immediately as a restore source:

```json
{
  "path": "backups/<name>/<name>_20260614-091401_v42.sql.gz",
  "source": "backups",
  "size_bytes": 928374829,
  "modified": "2026-06-14T09:14:01+00:00"
}
```

### `POST /instances/<name>/upgrade`

Swap the running WAR (version bump or specific WAR), preserving the DB and
volumes. Returns **202 + job**.

Body (exactly one of `version` / `war_url` / `war_file` required):

```json
{
  "version":  "2.42.4",
  "war_url":  "https://…/dhis.war",   // admin only
  "war_file": "/abs/path.war",        // admin only
  "tomcat":   "10",                   // optional
  "backup_first": true                // optional, default true
}
```

- `version` matches `^[0-9][0-9.]{0,15}$`; major-only resolves at job-run time.
- Version transitions: downgrades and major-version skips are rejected with
  `400`; same-major and one-major-up are allowed. (Not enforced for
  `war_url`/`war_file`, whose version can't be read in advance.)
- `tomcat` (`"9"`/`"10"`): changing the servlet container is **not yet
  supported** — a value differing from the instance's current Tomcat (or that
  can't be matched) → `400`.
- `backup_first` (default `true`) prepends a `d2-db-backup` step; its path is
  written to the job log.

On success, `result` is the `GET /instances` element with a best-effort
`dhis2_major_version`. Because Flyway migrates asynchronously on Tomcat boot,
this may still read the pre-upgrade major; re-poll `GET /instances?full=1`
once the instance is back up to observe the migrated version.
````

- [ ] **Step 2: Extend the `op` enum**

In the `GET /jobs/<id>` example (~line 269) and the job-model section, change the `op` comment from `create | reset | start | stop | delete` to `create | reset | start | stop | delete | backup | upgrade`. Also update the sentence "Anything that changes state … (create, reset, start, stop, delete)" near line 65 to include `backup` and `upgrade`, and the "For `create`, `reset`, and `start`, the job's `result` …" sentence (~line 98) to note that `upgrade` returns a `GET /instances` element and `backup` returns a `GET /seeds` element.

- [ ] **Step 3: Add validation cheat-sheet rows**

In the §6 validation cheat-sheet table (~line 311), add rows:

```markdown
| `label` (backup) | `^[a-z0-9][a-z0-9_-]{0,39}$` |
| `backup_first` | boolean, default `true` |
```

- [ ] **Step 4: Commit**

```bash
git add docs/broker-api.md
git commit -m "docs: document backup + upgrade broker endpoints"
```

---

## Self-review notes

- **Spec coverage:** backup endpoint (Tasks 5,6), upgrade endpoint (Task 7), label flag (Task 5), transition guard (Tasks 2,7), tomcat defer (Tasks 3,7), backup result shape (Tasks 4,6), upgrade result + version caveat (Task 6 Step 3), admin-only/agent-403 scope (Tasks 6,7), docs (Task 9), testing (Tasks 1-4 unit, Task 8 live). All spec sections map to a task.
- **Type consistency:** `extract_target_major` → `int|None`; `transition_error(int|None, int|None) → str|None`; `parse_tomcat_major(str) → int|None`; `current_tomcat_major(str) → int|None`; `dump_element(Path, str, Path) → dict`. Names used identically across Tasks 1-7.
- **Worker result block:** added once in Task 6 Step 3 covering both `upgrade` and `backup`; Task 7 only adds the route, so the two tasks don't both edit the same block.
