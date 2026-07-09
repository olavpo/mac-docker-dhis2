# Explicit ports + collision-free auto-select — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `d2-instance-create` auto-select host ports that don't collide with any instance (running or stopped), validate explicit `-p`/`-g` up front, and make the `d2-broker` `POST /instances` endpoint honor `http_port`/`pg_port`.

**Architecture:** Two shared bash helpers in `d2-lib.sh` (`configured_ports`, `port_available`) centralize "is this host port free?" — accounting for ports reserved in stopped instances' compose files. `d2-instance-create` uses them in both auto-select loops and to validate explicit ports before any side effects. The broker adds a pure `valid_port` format check and passes `-p`/`-g` through.

**Tech Stack:** Bash, Python 3 (stdlib `unittest`). No new frameworks — inline `bash -c` assertions + the existing broker unit suite.

**Spec:** `docs/superpowers/specs/2026-07-09-broker-explicit-ports-design.md`

---

## File structure

- `bash-scripts-docker/d2-lib.sh` — add `configured_ports`, `port_available`.
- `bash-scripts-docker/d2-instance-create` — rewrite both auto-select loops to use `port_available`; add explicit-port validation in the `else` branches.
- `bash-scripts-docker/d2-broker` — add `valid_port`; read/validate/pass `http_port`/`pg_port` in `create_instance`.
- `bash-scripts-docker/test_d2_broker.py` — unit tests for `valid_port`.
- `docs/broker-api.md`, `docs/broker.md`, `skills/dhis2-instances/SKILL.md` — docs.

---

## Task 1: `d2-lib.sh` port helpers

**Files:**
- Modify: `bash-scripts-docker/d2-lib.sh`

- [ ] **Step 1: Write the failing test (inline assertion).** Run this — it's also the acceptance check:

```bash
bash -c '
set -euo pipefail
BASE=$(mktemp -d)
mkdir -p "$BASE/inst-a" "$BASE/inst-b"
printf "    ports:\n      - \"9010:8080\"\n" > "$BASE/inst-a/docker-compose.yml"
printf "    ports:\n      - \"5433:5432\"\n" > "$BASE/inst-b/docker-compose.yml"
export DHIS2_BASE="$BASE"
. bash-scripts-docker/d2-lib.sh
fail=0
got=$(configured_ports | sort | tr "\n" " ")
[ "$got" = "5433 9010 " ] || { echo "FAIL configured_ports: [$got]"; fail=1; }
port_available 9010 && { echo "FAIL: 9010 should be reserved"; fail=1; } || true
port_available 5433 && { echo "FAIL: 5433 should be reserved"; fail=1; } || true
port_available 59999 || { echo "FAIL: 59999 should be available"; fail=1; }
EMPTY=$(mktemp -d); export DHIS2_BASE="$EMPTY"
[ -z "$(configured_ports)" ] || { echo "FAIL: empty base should print nothing"; fail=1; }
rm -rf "$BASE" "$EMPTY"
[ "$fail" = 0 ] && echo ALL_OK || exit 1
'
```

- [ ] **Step 2: Run it to verify it fails**

Expected: fails — `configured_ports: command not found` and non-zero exit (the `got=$(configured_ports …)` assignment aborts under `set -euo pipefail`).

- [ ] **Step 3: Add the helpers.** In `bash-scripts-docker/d2-lib.sh`, immediately after the `is_port_in_use()` function's closing `}`, add:

```bash
# Host ports already mapped in any instance's compose (tomcat 8080 and db 5432
# mappings). One port per line; empty (exit 0) if none match.
configured_ports() {
  local base="${DHIS2_BASE:-}"
  [ -n "$base" ] || return 0
  grep -hoE '[0-9]+:(8080|5432)' "$base"/*/docker-compose.yml 2>/dev/null \
    | cut -d: -f1 || true
}

# Exit 0 if a host port is free to claim: not reserved by an instance compose,
# not published by a running container, not held by a host listener.
port_available() {
  local port="$1"
  if configured_ports | grep -qx "$port"; then return 1; fi
  if docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":$port->"; then return 1; fi
  if is_port_in_use "$port"; then return 1; fi
  return 0
}
```

- [ ] **Step 4: Run the Step 1 assertion again to verify it passes**

Expected: `ALL_OK`.

- [ ] **Step 5: Commit**

```bash
git add bash-scripts-docker/d2-lib.sh
git commit -m "feat(lib): configured_ports + port_available helpers"
```

(Append to every commit body in this plan:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`)

---

## Task 2: collision-free auto-select + explicit-port validation in `d2-instance-create`

**Files:**
- Modify: `bash-scripts-docker/d2-instance-create`

`d2-lib.sh` is already sourced near the top. The current HTTP block reads:

```bash
# Auto-select HTTP port if not specified
if [ -z "$HTTP_PORT" ]; then
  echo "Auto-selecting HTTP port..."
  for port in $(seq 9010 9050); do
    if ! docker ps --format '{{.Ports}}' | grep -q ":$port->"; then
      if ! is_port_in_use "$port"; then
        HTTP_PORT=$port
        echo "Selected HTTP port: $HTTP_PORT"
        break
      fi
    fi
  done

  if [ -z "$HTTP_PORT" ]; then
    echo "Error: No available HTTP ports found in range 9010-9050"
    exit 1
  fi
fi
```

and the PG block is the same shape (`seq 5433 5999`, "PostgreSQL"/"5433-5999"). Both run before the instance-exists check, `mkdir`, and `docker compose up`.

- [ ] **Step 1: Replace the HTTP block** with:

```bash
# HTTP port: auto-select a non-colliding one, or validate an explicit -p.
if [ -z "$HTTP_PORT" ]; then
  echo "Auto-selecting HTTP port..."
  for port in $(seq 9010 9050); do
    if port_available "$port"; then
      HTTP_PORT=$port
      echo "Selected HTTP port: $HTTP_PORT"
      break
    fi
  done
  if [ -z "$HTTP_PORT" ]; then
    echo "Error: No available HTTP ports found in range 9010-9050"
    exit 1
  fi
elif ! port_available "$HTTP_PORT"; then
  echo "Error: HTTP port $HTTP_PORT is already in use or reserved by another instance" >&2
  exit 1
fi
```

- [ ] **Step 2: Replace the PG block** with:

```bash
# PostgreSQL port: auto-select a non-colliding one, or validate an explicit -g.
if [ -z "$PG_PORT" ]; then
  echo "Auto-selecting PostgreSQL port..."
  for port in $(seq 5433 5999); do
    if port_available "$port"; then
      PG_PORT=$port
      echo "Selected PostgreSQL port: $PG_PORT"
      break
    fi
  done
  if [ -z "$PG_PORT" ]; then
    echo "Error: No available PostgreSQL ports found in range 5433-5999"
    exit 1
  fi
elif ! port_available "$PG_PORT"; then
  echo "Error: PostgreSQL port $PG_PORT is already in use or reserved by another instance" >&2
  exit 1
fi
```

- [ ] **Step 3: Syntax check**

Run: `bash -n bash-scripts-docker/d2-instance-create && echo SYNTAX_OK`
Expected: `SYNTAX_OK`.

- [ ] **Step 4: Verify explicit-taken-port fails fast with NO side effects (no Docker daemon interaction needed beyond a `docker ps` read).**

```bash
BASE=$(mktemp -d); mkdir -p "$BASE/existing"
printf '    ports:\n      - "9010:8080"\n      - "5433:5432"\n' > "$BASE/existing/docker-compose.yml"
echo "--- explicit -p 9010 (reserved) expect exit 1, no new dir ---"
DHIS2_BASE="$BASE" bash bash-scripts-docker/d2-instance-create -v 2.42 -p 9010 zzz-port 2>&1 | grep -i "already in use"; echo "exit=${PIPESTATUS[0]}"
echo "--- explicit -g 5433 (reserved) expect exit 1 ---"
DHIS2_BASE="$BASE" bash bash-scripts-docker/d2-instance-create -v 2.42 -g 5433 -p 9099 zzz-port2 2>&1 | grep -i "already in use"; echo "exit=${PIPESTATUS[0]}"
echo "--- confirm no zzz-* dirs created ---"
ls -d "$BASE"/zzz-* 2>/dev/null && echo "LEAK" || echo "clean (good)"
rm -rf "$BASE"
```
Expected: each `grep` prints the "already in use or reserved" line (proving the message fired) and `exit=1` (`PIPESTATUS[0]` is the create script's exit — it aborted); final line `clean (good)`. (The reserved `9010`/`5433` come from the fake `existing` instance's compose, so `port_available` rejects them regardless of Docker state.)

- [ ] **Step 5: Commit**

```bash
git add bash-scripts-docker/d2-instance-create
git commit -m "feat(create): collision-free port auto-select; validate explicit -p/-g"
```

---

## Task 3: broker `valid_port` + `http_port`/`pg_port` on create

**Files:**
- Modify: `bash-scripts-docker/d2-broker` (add `valid_port`; extend `create_instance`)
- Modify: `bash-scripts-docker/test_d2_broker.py` (unit tests)

- [ ] **Step 1: Append the failing test** to `bash-scripts-docker/test_d2_broker.py` (after the last test class, before `if __name__`):

```python
class ValidPort(unittest.TestCase):
    def test_valid(self):
        for p in (1024, 65535, 8080, "9010"):
            self.assertTrue(broker.valid_port(p), p)

    def test_invalid(self):
        for p in (1023, 70000, 0, "abc", None, "80.5", ""):
            self.assertFalse(broker.valid_port(p), p)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker -v`
Expected: FAIL — `AttributeError: module 'd2broker' has no attribute 'valid_port'`.

- [ ] **Step 3: Add `valid_port`.** In `bash-scripts-docker/d2-broker`, immediately after the `mem_to_mb` function, add:

```python
def valid_port(n):
    """True iff n is an integer host port in [1024, 65535]."""
    try:
        p = int(n)
    except (TypeError, ValueError):
        return False
    return 1024 <= p <= 65535
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd bash-scripts-docker && python3 -m unittest test_d2_broker`
Expected: `OK` (2 more tests than before).

- [ ] **Step 5: Pass the fields in `create_instance`.** Find the tail of `create_instance` where the `memory` block is followed by `argv.append(name)`:

```python
        if body.get("memory"):
            self.validate_memory(body["memory"], scope)
            argv += ["-m", str(body["memory"])]
        argv.append(name)
```

and change it to insert the port handling before `argv.append(name)`:

```python
        if body.get("memory"):
            self.validate_memory(body["memory"], scope)
            argv += ["-m", str(body["memory"])]
        for field, flag in (("http_port", "-p"), ("pg_port", "-g")):
            val = body.get(field)
            if val is not None:
                if not valid_port(val):
                    raise ApiError(400, f"{field} must be an integer 1024-65535")
                argv += [flag, str(int(val))]
        argv.append(name)
```

- [ ] **Step 6: Verify module imports and full suite passes**

Run: `cd bash-scripts-docker && python3 -c "import importlib.util,importlib.machinery; l=importlib.machinery.SourceFileLoader('b','d2-broker'); m=importlib.util.module_from_spec(importlib.util.spec_from_loader('b',l)); l.exec_module(m); print('import ok')" && python3 -m unittest test_d2_broker`
Expected: `import ok`, then `OK`.

- [ ] **Step 7: Commit**

```bash
git add bash-scripts-docker/d2-broker bash-scripts-docker/test_d2_broker.py
git commit -m "feat(broker): honor http_port/pg_port on POST /instances"
```

---

## Task 4: documentation

**Files:**
- Modify: `docs/broker-api.md`, `docs/broker.md`, `skills/dhis2-instances/SKILL.md`

READ each file first for exact anchors.

- [ ] **Step 1: `docs/broker-api.md`.** In the `POST /instances` body example, add after the `memory` line:

```json
  "http_port": 9010,             // optional; host HTTP port. Auto-selected (free) if omitted
  "pg_port": 5433,               // optional; host Postgres port. Auto-selected (free) if omitted
```

In that endpoint's validation list, add:

```markdown
- `http_port` / `pg_port` are integers `1024–65535` (else `400`). Omitted →
  the broker auto-selects a port not used or reserved by any other instance
  (running or stopped). An explicit port already in use/reserved fails the job.
```

- [ ] **Step 2: `docs/broker.md`.** In the `POST /instances` endpoint-table row, add `"http_port"?, "pg_port"?` to the body list. After the table, add a sentence:

```markdown
`http_port`/`pg_port` are optional (integers 1024–65535); omitted, the broker
auto-selects a port not used or reserved by any other instance (running or
stopped), so new instances no longer collide with stopped ones.
```

- [ ] **Step 3: `skills/dhis2-instances/SKILL.md`.** In "What you can and cannot do", add a bullet:

```markdown
- Ports: you normally reach instances via `devnet_url`, so you rarely need host
  ports. If you do, pass `http_port`/`pg_port` (integers 1024–65535) on create;
  omit them to let the broker pick free, non-colliding ports.
```

- [ ] **Step 4: Commit**

```bash
git add docs/broker-api.md docs/broker.md skills/dhis2-instances/SKILL.md
git commit -m "docs: document http_port/pg_port and collision-free auto-select"
```

---

## Task 5: live end-to-end verification (optional; needs Docker)

**Files:** none (verification only).

- [ ] **Step 1: Auto-select avoids a stopped instance's reserved port.** Ensure at least one *stopped* instance reserves `9010` in its compose (e.g. `acdc`). Create a new instance with no ports and confirm it does NOT get `9010`:

```bash
d2-instance-create -v 2.42 agent-port-check
grep -E '[0-9]+:8080' "$DHIS2_BASE/agent-port-check/docker-compose.yml"   # expect a port != 9010
d2-instance-delete agent-port-check
```

- [ ] **Step 2: Explicit taken port fails fast.**

```bash
d2-instance-create -v 2.42 -p 9010 agent-port-taken   # expect: "HTTP port 9010 is already in use or reserved", exit 1, no dir
ls -d "$DHIS2_BASE/agent-port-taken" 2>/dev/null && echo LEAK || echo "clean (good)"
```

- [ ] **Step 3: Broker end-to-end (alternate-port broker).** `POST /instances {"name":"agent-p","version":"2.42","http_port":9044,"pg_port":5444}` → job succeeds, compose shows `9044`/`5444`; `POST` with `http_port: 70000` → `400`. Then delete `agent-p`.

- [ ] **Step 4: Commit (only if a fix was needed).**

---

## Self-review notes

- **Spec coverage:** helpers (Task 1); auto-select rewrite + explicit validation, side-effect-free (Task 2); broker `valid_port` + field passthrough with `400` on bad format (Task 3); docs across three surfaces (Task 4); live (Task 5). Availability enforced single-source in the script; broker validates format only — matches spec §3.
- **Naming consistency:** `configured_ports`, `port_available`, `valid_port`, `http_port`/`pg_port`, flags `-p`/`-g` used identically across tasks.
- **Ordering:** port resolution/validation stays where the existing loops are — before the instance-exists check, `mkdir`, and `docker compose up` — so Task 2 Step 4's no-side-effects assertion holds.
