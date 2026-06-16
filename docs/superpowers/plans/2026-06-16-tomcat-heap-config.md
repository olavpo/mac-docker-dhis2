# Configurable Tomcat heap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users set the Tomcat max heap (`-Xmx`) for a DHIS2 instance at create time and on an existing instance, via the CLI and the `d2-broker` HTTP API, defaulting to 4g.

**Architecture:** Heap is appended to `CATALINA_OPTS`. The compose templates carry a `${JAVA_MEM_OPTS}` placeholder that `d2-instance-create` fills (default 4g); a new `d2-set-memory` script rewrites the `CATALINA_OPTS` line of an existing instance's compose file and recreates Tomcat. The broker adds a `memory` field to create and a new `POST /instances/<name>/memory` endpoint, both capping agent-token requests.

**Tech Stack:** Python 3 (stdlib only, `unittest`), Bash, Docker, docker compose.

**Spec:** `docs/superpowers/specs/2026-06-16-tomcat-heap-config-design.md`

---

## File structure

- `bash-scripts-docker/d2-broker` — modify: `MEMORY_RE`, `mem_to_mb`, a `validate_memory` handler helper, `memory` on create, new `set_memory_instance` handler + route, worker result branch.
- `bash-scripts-docker/d2-instance-create` — modify: `-m` option, 4g default, template substitution.
- `_templates/docker-compose-tomcat9.yml`, `_templates/docker-compose-tomcat10.yml` — modify: `CATALINA_OPTS` placeholder.
- `bash-scripts-docker/d2-set-memory` — create: change heap on an existing instance.
- `bash-scripts-docker/test_d2_broker.py` — modify: unit tests for `mem_to_mb` + `MEMORY_RE`.
- `docs/broker-api.md`, `docs/broker.md`, `skills/dhis2-instances/SKILL.md`, `README.md` — modify: documentation.

The test file loads the extension-less `d2-broker` as module `broker` via `importlib.machinery.SourceFileLoader` (already set up; 15 tests passing).

---

## Task 1: broker `mem_to_mb` + `MEMORY_RE`

Pure helper + regex for heap-size parsing/validation.

**Files:**
- Modify: `bash-scripts-docker/d2-broker` (add `MEMORY_RE` after `VERSION_RE`; add `mem_to_mb` after `extract_target_major`)
- Modify: `bash-scripts-docker/test_d2_broker.py` (append two test classes)

- [ ] **Step 1: Append the failing tests** to `bash-scripts-docker/test_d2_broker.py` (after the last existing test class, before `if __name__`):

```python
class MemToMb(unittest.TestCase):
    def test_megabytes(self):
        self.assertEqual(broker.mem_to_mb("512m"), 512)

    def test_gigabytes(self):
        self.assertEqual(broker.mem_to_mb("2g"), 2048)

    def test_four_g(self):
        self.assertEqual(broker.mem_to_mb("4g"), 4096)

    def test_uppercase_unit(self):
        self.assertEqual(broker.mem_to_mb("1G"), 1024)

    def test_unparseable_returns_none(self):
        for bad in ("", "4", "4gb", "abc", "4k", "2.5g"):
            self.assertIsNone(broker.mem_to_mb(bad))


class MemoryRe(unittest.TestCase):
    def test_matches(self):
        for good in ("512m", "2g", "4096m", "1G", "8M"):
            self.assertIsNotNone(broker.MEMORY_RE.match(good))

    def test_rejects(self):
        for bad in ("", "4", "4gb", "4k", "2.5g", " 2g"):
            self.assertIsNone(broker.MEMORY_RE.match(bad))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: FAIL — `AttributeError: module 'd2broker' has no attribute 'mem_to_mb'` (and `MEMORY_RE`).

- [ ] **Step 3: Add the regex.** In `bash-scripts-docker/d2-broker`, find:

```python
VERSION_RE = re.compile(r"^[0-9][0-9.]{0,15}$")
```

and add immediately after it:

```python
MEMORY_RE = re.compile(r"^[0-9]+[mMgG]$")
```

- [ ] **Step 4: Add the helper.** In `bash-scripts-docker/d2-broker`, find the end of the `extract_target_major` function:

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

and add immediately after it:

```python
def mem_to_mb(size):
    """Heap size string ('512m', '2g') to megabytes (int). None if
    unparseable. Only m/g (case-insensitive) are accepted."""
    m = re.match(r"^([0-9]+)([mMgG])$", str(size))
    if not m:
        return None
    n = int(m.group(1))
    return n * 1024 if m.group(2) in "gG" else n
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: PASS (25 tests total).

- [ ] **Step 6: Commit**

```bash
git add bash-scripts-docker/d2-broker bash-scripts-docker/test_d2_broker.py
git commit -m "feat(broker): mem_to_mb helper + MEMORY_RE"
```

(Append to every commit body in this plan:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`)

---

## Task 2: templates + `d2-instance-create -m` (4g default)

Set heap at create time, defaulting to 4g.

**Files:**
- Modify: `_templates/docker-compose-tomcat10.yml`, `_templates/docker-compose-tomcat9.yml`
- Modify: `bash-scripts-docker/d2-instance-create`

- [ ] **Step 1: Add the placeholder to both templates.** In `_templates/docker-compose-tomcat10.yml` and `_templates/docker-compose-tomcat9.yml`, change the `CATALINA_OPTS` line from:

```yaml
      CATALINA_OPTS: "-Ddhis2.home=/DHIS2_home -Dlog4j2.configurationFile=/DHIS2_home/log4j2.xml"
```

to:

```yaml
      CATALINA_OPTS: "-Ddhis2.home=/DHIS2_home -Dlog4j2.configurationFile=/DHIS2_home/log4j2.xml${JAVA_MEM_OPTS}"
```

- [ ] **Step 2: Add the default + option in `d2-instance-create`.** Find the defaults block:

```bash
WAR_FILE=""
TOMCAT_VERSION="10"  # Default to Tomcat 10
```

and change it to:

```bash
WAR_FILE=""
TOMCAT_VERSION="10"  # Default to Tomcat 10
MEMORY=""
DEFAULT_MEMORY="${D2_DEFAULT_MEMORY:-4g}"  # Tomcat max heap when -m omitted
```

Then change the getopts line:

```bash
while getopts "v:l:s:p:g:f:t:" opt; do
```

to:

```bash
while getopts "v:l:s:p:g:f:t:m:" opt; do
```

and add a case inside the `case $opt in` block, after the `t)` line:

```bash
    m) MEMORY=$OPTARG ;;
```

- [ ] **Step 3: Validate + build the opts string.** Find the abspath block that ends with:

```bash
if [ -n "$DB_SEED" ]; then
  DB_SEED=$(abspath "$DB_SEED")
fi
```

and add immediately after it:

```bash
# Resolve and validate the Tomcat max heap (default 4g; see D2_DEFAULT_MEMORY)
MEMORY="${MEMORY:-$DEFAULT_MEMORY}"
if ! echo "$MEMORY" | grep -qE '^[0-9]+[mMgG]$'; then
  echo "Error: -m memory must match ^[0-9]+[mMgG]\$ (e.g. 512m, 2g)"
  exit 1
fi
JAVA_MEM_OPTS=" -Xmx${MEMORY}"
```

- [ ] **Step 4: Substitute into the template.** Find the compose-creation `sed`:

```bash
sed "s/\${PG_PORT:-5432}/$PG_PORT/g; \
     s/\${HTTP_PORT:-8080}/$HTTP_PORT/g; \
     s/\${INSTANCE_NAME}/$INSTANCE/g" \
  "$TEMPLATE_FILE" > "$DHIS2_HOME/docker-compose.yml"
```

and change it to (adds the `JAVA_MEM_OPTS` line):

```bash
sed "s/\${PG_PORT:-5432}/$PG_PORT/g; \
     s/\${HTTP_PORT:-8080}/$HTTP_PORT/g; \
     s/\${JAVA_MEM_OPTS}/$JAVA_MEM_OPTS/g; \
     s/\${INSTANCE_NAME}/$INSTANCE/g" \
  "$TEMPLATE_FILE" > "$DHIS2_HOME/docker-compose.yml"
```

- [ ] **Step 5: Update usage + summary.** In `usage()`, after the `-t <version>` line, add:

```bash
  echo "  -m <size>       Java max heap, e.g. 2g (default: 4g / \$D2_DEFAULT_MEMORY)"
```

In the create summary, find:

```bash
echo "  PostgreSQL Port: $PG_PORT"
```

and add immediately after it:

```bash
echo "  Tomcat heap:     -Xmx$MEMORY"
```

- [ ] **Step 6: Syntax check + render check (no Docker).**

Run: `bash -n bash-scripts-docker/d2-instance-create && echo SYNTAX_OK`
Expected: `SYNTAX_OK`.

Run (simulates the substitution the script does):
```bash
JAVA_MEM_OPTS=" -Xmx4g"; sed "s/\${JAVA_MEM_OPTS}/$JAVA_MEM_OPTS/g" \
  _templates/docker-compose-tomcat10.yml | grep CATALINA_OPTS
```
Expected: the line ends with `...log4j2.xml -Xmx4g"` and contains no `${JAVA_MEM_OPTS}`.

- [ ] **Step 7: Commit**

```bash
git add _templates/docker-compose-tomcat9.yml _templates/docker-compose-tomcat10.yml bash-scripts-docker/d2-instance-create
git commit -m "feat(create): -m max-heap option, default 4g (-Xmx via CATALINA_OPTS)"
```

---

## Task 3: `d2-set-memory` (change heap on an existing instance)

**Files:**
- Create: `bash-scripts-docker/d2-set-memory`

- [ ] **Step 1: Write the script.** Create `bash-scripts-docker/d2-set-memory` with exactly:

```bash
#!/bin/bash
#       ____  __  ______________
#      / __ \/ / / /  _/ ___/__ \
#     / / / / /_/ // / \__ \__/ /
#    / /_/ / __  // / ___/ / __/
#   /_____/_/ /_/___//____/____/
#
#   Set the Tomcat max heap (-Xmx) for an existing DHIS2 instance and
#   recreate its Tomcat container. Database and volumes are preserved.

set -euo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/d2-lib.sh"

if [ -z "${DHIS2_BASE:-}" ]; then
  echo "Error: DHIS2_BASE environment variable not set"
  exit 1
fi

usage() {
  echo "Usage: $0 <instance name> <size>"
  echo "Sets the Tomcat max heap (-Xmx) and recreates the Tomcat container."
  echo "  <size>   e.g. 512m, 2g (matches ^[0-9]+[mMgG]\$)"
  exit 1
}

if [ $# -ne 2 ]; then
  usage
fi

INSTANCE=$1
SIZE=$2
DHIS2_HOME="$DHIS2_BASE/$INSTANCE"

if [ ! -d "$DHIS2_HOME" ]; then
  echo "Error: Instance $INSTANCE does not exist"
  exit 1
fi

if ! echo "$SIZE" | grep -qE '^[0-9]+[mMgG]$'; then
  echo "Error: size must match ^[0-9]+[mMgG]\$ (e.g. 512m, 2g)"
  exit 1
fi

COMPOSE="$DHIS2_HOME/docker-compose.yml"
if [ ! -f "$COMPOSE" ]; then
  echo "Error: $COMPOSE not found"
  exit 1
fi

# Rewrite the CATALINA_OPTS line: drop any existing -Xmx token, then append the
# new one before the line's closing quote. Temp-file (not sed -i) for macOS/
# Linux portability. Assumes CATALINA_OPTS is a single double-quoted string
# ending its line (true for the compose templates).
TMP=$(mktemp)
sed -E "/CATALINA_OPTS:/{s/ -Xmx[0-9]+[mMgGkK]?//g;s/\"\$/ -Xmx${SIZE}\"/;}" \
  "$COMPOSE" > "$TMP"
mv "$TMP" "$COMPOSE"

echo "Set Tomcat heap for $INSTANCE to -Xmx$SIZE; recreating container..."
cd "$DHIS2_HOME"
docker compose up -d

echo "✅ Tomcat heap for $INSTANCE is now -Xmx$SIZE"
```

- [ ] **Step 2: Make it executable**

Run: `chmod +x bash-scripts-docker/d2-set-memory`

- [ ] **Step 3: Syntax check**

Run: `bash -n bash-scripts-docker/d2-set-memory && echo SYNTAX_OK`
Expected: `SYNTAX_OK`.

- [ ] **Step 4: Verify the rewrite logic on a fixture (no Docker).**

Run:
```bash
TMPF=$(mktemp)
printf '      CATALINA_OPTS: "-Ddhis2.home=/DHIS2_home -Dlog4j2.configurationFile=/DHIS2_home/log4j2.xml"\n' > "$TMPF"
SIZE=1g; sed -E "/CATALINA_OPTS:/{s/ -Xmx[0-9]+[mMgGkK]?//g;s/\"\$/ -Xmx${SIZE}\"/;}" "$TMPF"
# now test replacing an existing -Xmx
printf '      CATALINA_OPTS: "-Ddhis2.home=/DHIS2_home -Xmx4g"\n' > "$TMPF"
SIZE=1g; sed -E "/CATALINA_OPTS:/{s/ -Xmx[0-9]+[mMgGkK]?//g;s/\"\$/ -Xmx${SIZE}\"/;}" "$TMPF"
rm -f "$TMPF"
```
Expected: first line ends with `...log4j2.xml -Xmx1g"`; second ends with `...DHIS2_home -Xmx1g"` (the old `-Xmx4g` replaced, not duplicated).

- [ ] **Step 5: Commit**

```bash
git add bash-scripts-docker/d2-set-memory
git commit -m "feat: d2-set-memory to change Tomcat heap on an existing instance"
```

---

## Task 4: broker — `memory` on create + agent cap

**Files:**
- Modify: `bash-scripts-docker/d2-broker` (add `validate_memory` helper method; use it in `create_instance`)

- [ ] **Step 1: Add the `validate_memory` helper.** In the `Handler` class, find the `reset_instance` method and add this method immediately before it (anywhere in the `Handler` class is fine; before `reset_instance` keeps it near the other validators):

```python
    def validate_memory(self, memory, scope):
        """Validate a heap size and enforce the agent cap. Raises ApiError."""
        if not MEMORY_RE.match(str(memory)):
            raise ApiError(400, "memory must match ^[0-9]+[mMgG]$ "
                                "(e.g. 512m, 2g)")
        if scope != "all":
            cap = os.environ.get("D2_BROKER_MAX_AGENT_MEMORY", "8g")
            if mem_to_mb(str(memory)) > mem_to_mb(cap):
                raise ApiError(400, f"requested heap {memory} exceeds the "
                                    f"agent cap ({cap})")
```

- [ ] **Step 2: Use it in `create_instance`.** In `create_instance`, find the tomcat block followed by `argv.append(name)`:

```python
        if body.get("tomcat"):
            if str(body["tomcat"]) not in ("9", "10"):
                raise ApiError(400, "tomcat must be 9 or 10")
            argv += ["-t", str(body["tomcat"])]
        argv.append(name)
```

and change it to insert the memory handling before `argv.append(name)`:

```python
        if body.get("tomcat"):
            if str(body["tomcat"]) not in ("9", "10"):
                raise ApiError(400, "tomcat must be 9 or 10")
            argv += ["-t", str(body["tomcat"])]
        if body.get("memory"):
            self.validate_memory(body["memory"], scope)
            argv += ["-m", str(body["memory"])]
        argv.append(name)
```

- [ ] **Step 3: Verify the module imports and tests pass**

Run: `cd bash-scripts-docker && python3 -c "import importlib.util,importlib.machinery; l=importlib.machinery.SourceFileLoader('b','d2-broker'); m=importlib.util.module_from_spec(importlib.util.spec_from_loader('b',l)); l.exec_module(m); print('import ok')" && python3 -m unittest test_d2_broker`
Expected: `import ok`, then `OK` (25 tests). Endpoint behaviour is verified live in Task 7.

- [ ] **Step 4: Commit**

```bash
git add bash-scripts-docker/d2-broker
git commit -m "feat(broker): memory field on POST /instances (agent-capped)"
```

---

## Task 5: broker — `POST /instances/<name>/memory`

The `validate_memory` helper and the worker's instance-element branch are reused/extended here.

**Files:**
- Modify: `bash-scripts-docker/d2-broker` (route, `set_memory_instance` handler, worker op set)

- [ ] **Step 1: Route the action.** In `post_api`, find the upgrade route:

```python
            if action == "upgrade":
                return self.upgrade_instance(name, scope)
```

and add immediately after it:

```python
            if action == "memory":
                return self.set_memory_instance(name, scope)
```

- [ ] **Step 2: Add the handler.** Add this method to the `Handler` class, immediately after `upgrade_instance`:

```python
    def set_memory_instance(self, name, scope):
        body = self.read_body()
        memory = body.get("memory")
        if not memory:
            raise ApiError(400, 'memory is required (e.g. {"memory":"2g"})')
        self.validate_memory(memory, scope)
        self.require_instance(name)
        self.check_no_active_job(name)
        argv = [str(SCRIPT_DIR / "d2-set-memory"), name, str(memory)]
        return self.job_response(self.jobs.submit("memory", name, [argv]))
```

- [ ] **Step 3: Extend the worker result branch.** In `JobManager._worker`, find:

```python
                    if job["op"] in ("create", "reset", "start", "upgrade"):
```

and change it to:

```python
                    if job["op"] in ("create", "reset", "start", "upgrade",
                                     "memory"):
```

- [ ] **Step 4: Verify the module imports and tests pass**

Run: `cd bash-scripts-docker && python3 -c "import importlib.util,importlib.machinery; l=importlib.machinery.SourceFileLoader('b','d2-broker'); m=importlib.util.module_from_spec(importlib.util.spec_from_loader('b',l)); l.exec_module(m); print('import ok')" && python3 -m unittest test_d2_broker`
Expected: `import ok`, then `OK` (25 tests).

- [ ] **Step 5: Commit**

```bash
git add bash-scripts-docker/d2-broker
git commit -m "feat(broker): POST /instances/<name>/memory endpoint"
```

---

## Task 6: documentation

**Files:**
- Modify: `docs/broker-api.md`, `docs/broker.md`, `skills/dhis2-instances/SKILL.md`, `README.md`

- [ ] **Step 1: `docs/broker-api.md` — create body.** In the `POST /instances` request body example, add a `memory` line after the `tomcat` line:

```json
  "tomcat": "10",                // optional; "9" or "10", default "10"
  "memory": "4g",                // optional; Tomcat max heap (-Xmx), default 4g
```

Under that endpoint's Validation list, add:

```markdown
- `memory` matches `^[0-9]+[mMgG]$` (e.g. `512m`, `2g`); default `4g` when
  omitted. Agent scope: heap above `D2_BROKER_MAX_AGENT_MEMORY` (default `8g`)
  → `400`.
```

- [ ] **Step 2: `docs/broker-api.md` — new endpoint section.** After the `POST /instances/<name>/upgrade` section and before `GET /seeds`, add:

````markdown
### `POST /instances/<name>/memory`

Set the Tomcat max heap (`-Xmx`) on an existing instance and recreate the
Tomcat container (DB and volumes preserved). Returns **202 + job**.

Body:

```json
{ "memory": "2g" }   // required; matches ^[0-9]+[mMgG]$
```

- `memory` is required; `^[0-9]+[mMgG]$` (e.g. `512m`, `2g`).
- Agent scope: heap above `D2_BROKER_MAX_AGENT_MEMORY` (default `8g`) → `400`.
- `result` on success is the `GET /instances` element (like `start`). The
  instance restarts as Tomcat is recreated.
````

- [ ] **Step 3: `docs/broker-api.md` — op enum + cheat-sheet.** Change the `op` enum comment in the `GET /jobs/<id>` example to include `memory`:

```
  "op": "create",                        // create | reset | start | stop | delete | backup | upgrade | memory
```

In the §6 validation cheat-sheet table, add a row:

```markdown
| `memory` | `^[0-9]+[mMgG]$` (e.g. `512m`, `2g`); agent heap capped by `D2_BROKER_MAX_AGENT_MEMORY` (default `8g`) |
```

- [ ] **Step 4: `docs/broker.md` — endpoint table + env + agent note.** In the API endpoint table, after the `POST /instances/<name>/upgrade` row, add:

```markdown
| `POST /instances/<name>/memory` | Set Tomcat max heap (`-Xmx`) + recreate Tomcat. Body: `{"memory"}` → 202 job |
```

In the `POST /instances` create row, add `"memory"?` to its body list:

```markdown
| `POST /instances` | Create. Body: `{"name", "version"?, "seed"?, "tomcat"?, "memory"?, "war_url"?†, "war_file"?†}` → 202 job |
```

In the config env-var table, add two rows:

```markdown
| `D2_DEFAULT_MEMORY` | `4g` | Default Tomcat max heap for new instances (`d2-instance-create`) |
| `D2_BROKER_MAX_AGENT_MEMORY` | `8g` | Max heap an agent token may request |
```

In the agent-scope bullet list (security model), add a bullet:

```markdown
  - may set the Tomcat heap (`memory` on create, `POST .../memory`), but only
    up to `D2_BROKER_MAX_AGENT_MEMORY` (default `8g`);
```

- [ ] **Step 5: `skills/dhis2-instances/SKILL.md` — agent guidance.** After the "Upgrading an instance" paragraph, add:

```markdown
**Memory / heap**: instances default to `-Xmx4g`. Set a different heap at
create with `"memory":"2g"` in the body, or change it later with
`POST /instances/<name>/memory` `{"memory":"2g"}` (recreates Tomcat — a brief
restart; DB preserved). Your token is capped by `D2_BROKER_MAX_AGENT_MEMORY`
(default `8g`); a larger request returns `400`. Size format: `^[0-9]+[mMgG]$`
(e.g. `512m`, `2g`).
```

Also add a curl line in the Operations block, after the upgrade example:

```bash
# Change the Tomcat heap (recreates Tomcat; DB preserved)
curl -s -X POST -H "$H" -H "$CT" -d '{"memory":"2g"}' $B/instances/agent-mytest/memory
```

- [ ] **Step 6: `README.md` — flag, script, example.** In the script list under "Application & info" (or near `d2-deploy-war`), add a bullet:

```markdown
  - `d2-set-memory` — set the Tomcat max heap (`-Xmx`) on an instance + recreate Tomcat
```

In the create-instance usage/example area, add a note that `-m <size>` sets the heap (default 4g), e.g.:

```bash
d2-instance-create -v 2.42 -m 6g myinstance   # 6 GB max heap (default is 4g)
```

- [ ] **Step 7: Commit**

```bash
git add docs/broker-api.md docs/broker.md skills/dhis2-instances/SKILL.md README.md
git commit -m "docs: document configurable Tomcat heap (CLI + broker)"
```

---

## Task 7: live end-to-end verification

Requires Docker + `DHIS2_BASE`. Run a fresh broker with the new code on an
alternate port (the installed broker on 9300 stays untouched), exercise the
paths against a throwaway `agent-*` instance, then clean up.

**Files:** none (verification only).

- [ ] **Step 1: Start a fresh broker on an alternate port**

```bash
cd bash-scripts-docker
DHIS2_BASE="$DHIS2_BASE" D2_BROKER_PORT=9399 D2_BROKER_BIND=127.0.0.1 ./d2-broker run >/tmp/d2-broker-mem.log 2>&1 &
sleep 2
B=http://localhost:9399
TOKEN=$(python3 -c 'import json,os;print(json.load(open(os.environ["DHIS2_BASE"]+"/_broker/tokens.json"))["admin"]["token"])')
AGENT=$(python3 -c 'import json,os;print(json.load(open(os.environ["DHIS2_BASE"]+"/_broker/tokens.json"))["agent"]["token"])')
H="Authorization: Bearer $TOKEN"; AH="Authorization: Bearer $AGENT"
curl -s "$B/health"; echo
```
Expected: `{"status": "ok", ...}`.

Helper to wait for a job (paste once):
```bash
wait_job(){ while true; do s=$(curl -s -H "$H" "$B/jobs/$1"|python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])'); echo "$s"; case "$s" in succeeded|failed|interrupted) break;; esac; sleep 3; done; }
heap(){ docker exec "$1-tomcat-1" printenv CATALINA_OPTS 2>/dev/null || docker exec "$1_tomcat_1" printenv CATALINA_OPTS; }
```

- [ ] **Step 2: Create with explicit memory and assert `-Xmx2g`**

```bash
curl -s -X POST -H "$H" -d '{"name":"agent-mem","version":"2.42","memory":"2g"}' "$B/instances"   # note job id
# wait_job <id>
heap agent-mem
```
Expected: job `succeeded`; `CATALINA_OPTS` contains `-Xmx2g`.

- [ ] **Step 3: Assert the default is 4g on a create without memory**

```bash
curl -s -X POST -H "$H" -d '{"name":"agent-mem2","version":"2.42"}' "$B/instances"   # note job id
# wait_job <id>
heap agent-mem2
```
Expected: job `succeeded`; `CATALINA_OPTS` contains `-Xmx4g`. (Then delete agent-mem2: `curl -s -X DELETE -H "$H" "$B/instances/agent-mem2"` and `wait_job`.)

- [ ] **Step 4: Change heap on the existing instance to 1g**

```bash
curl -s -X POST -H "$H" -d '{"memory":"1g"}' "$B/instances/agent-mem/memory"   # note job id
# wait_job <id>
grep CATALINA_OPTS "$DHIS2_BASE/agent-mem/docker-compose.yml"
heap agent-mem
```
Expected: job `succeeded`; compose file and container `CATALINA_OPTS` now show `-Xmx1g` (no leftover `-Xmx2g`).

- [ ] **Step 5: Validation + agent-cap rejections**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "$H"  -d '{"memory":"2gb"}'  "$B/instances/agent-mem/memory"   # 400 (bad format)
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "$H"  -d '{}'                "$B/instances/agent-mem/memory"   # 400 (required)
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "$AH" -d '{"memory":"16g"}'  "$B/instances/agent-mem/memory"   # 400 (over 8g cap)
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "$AH" -d '{"memory":"2g"}'   "$B/instances/agent-mem/memory"   # 202 (within cap) -> wait_job, then it actually changes heap
```
Expected: `400`, `400`, `400`, `202`.

- [ ] **Step 6: Clean up**

```bash
curl -s -X DELETE -H "$H" "$B/instances/agent-mem"   # wait_job
pkill -f "d2-broker run"   # stops the alternate-port test broker
curl -s "$B/health" || echo "test broker stopped"
```

- [ ] **Step 7: Commit (only if Steps 1–6 surfaced fixes)**

Otherwise nothing to commit.

---

## Self-review notes

- **Spec coverage:** `-Xmx`-only + 4g default (Task 2), `D2_DEFAULT_MEMORY` (Task 2), set-at-create CLI (Task 2) + broker (Task 4), change-existing CLI `d2-set-memory` (Task 3) + broker endpoint (Task 5), agent cap `D2_BROKER_MAX_AGENT_MEMORY` 8g (Tasks 4–5), `MEMORY_RE` + `mem_to_mb` (Task 1), docs (Task 6), tests — unit (Task 1) + bash/render (Tasks 2–3) + live (Task 7). All spec sections map to a task.
- **Type consistency:** `mem_to_mb(str) -> int|None`; `MEMORY_RE` is a compiled regex; `validate_memory(self, memory, scope) -> None (raises ApiError)` defined in Task 4 and reused in Task 5; worker op set extended once (Task 5) to include `memory`. CLI/env names: `-m`, `D2_DEFAULT_MEMORY`, `D2_BROKER_MAX_AGENT_MEMORY`, `JAVA_MEM_OPTS` used consistently across tasks.
- **Coupling note:** Task 2 changes templates and `d2-instance-create` together (a template `${JAVA_MEM_OPTS}` with an un-updated creator would leave an un-substituted token), so they ship in one commit.
