# Tomcat/DHIS2 version-mismatch check — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `d2-instance-create` auto-select the correct Tomcat major for a given DHIS2 `-v` version (and abort on an explicit conflicting `-t`), so an incompatible Tomcat/DHIS2 pairing can't be created.

**Architecture:** Two pure helpers in `d2-lib.sh` (`dhis2_major`, `required_tomcat_for_major`) encode the boundary (≤41→Tomcat 9, ≥42→Tomcat 10). `d2-instance-create` uses them right after option parsing — before any side effects — to fill or validate `TOMCAT_VERSION`. The broker gains the behavior for free (it shells out to `d2-instance-create`); the boundary constant is single-sourced in `d2-lib.sh`.

**Tech Stack:** Bash. No new test framework — verify with `bash -n` and inline `bash -c 'source …'` assertions (the repo convention for shell tools).

**Spec:** `docs/superpowers/specs/2026-07-09-tomcat-version-check-design.md`

---

## File structure

- `bash-scripts-docker/d2-lib.sh` — add `dhis2_major` and `required_tomcat_for_major`.
- `bash-scripts-docker/d2-instance-create` — default `TOMCAT_VERSION` empty; add a `-t` value guard and the version→Tomcat derivation/validation; usage text.
- `docs/broker-api.md`, `docs/broker.md`, `skills/dhis2-instances/SKILL.md`, `README.md` — documentation.

---

## Task 1: `d2-lib.sh` helpers

**Files:**
- Modify: `bash-scripts-docker/d2-lib.sh`

- [ ] **Step 1: Write the failing test (an inline assertion script).**

Run this now (it is also the acceptance check for the task):

```bash
bash -c '
set -euo pipefail
. bash-scripts-docker/d2-lib.sh
fail=0
ck(){ got="$(eval "$1")"; if [ "$got" = "$2" ]; then echo "ok  : $1 = $got"; else echo "FAIL: $1 = $got (want $2)"; fail=1; fi; }
ck "dhis2_major 42" 42
ck "dhis2_major 2.42" 42
ck "dhis2_major 2.42.4" 42
ck "dhis2_major 41" 41
ck "dhis2_major 41.4" 41
ck "dhis2_major 2.41.3" 41
ck "required_tomcat_for_major 41" 9
ck "required_tomcat_for_major 40" 9
ck "required_tomcat_for_major 42" 10
ck "required_tomcat_for_major 43" 10
ck "required_tomcat_for_major abc" 10
[ "$fail" = 0 ] && echo ALL_OK || exit 1
'
```

- [ ] **Step 2: Run it to verify it fails**

Expected: fails — `dhis2_major: command not found` (or a non-zero exit) because the functions don't exist yet.

- [ ] **Step 3: Add the helpers.** In `bash-scripts-docker/d2-lib.sh`, immediately after the `normalize_version()` function (the closing `}` of `normalize_version`), add:

```bash
# DHIS2 major from a version string: 42, 2.42, 2.42.4, 41.4 -> 42 / 41.
dhis2_major() {
  local v="$1"
  if [[ "$v" =~ ^2\.([0-9]+) ]]; then
    echo "${BASH_REMATCH[1]}"        # strip a leading "2." (the DHIS2 2.x line)
  else
    echo "${v%%.*}"                  # first dot-separated component
  fi
}

# Tomcat major required by a DHIS2 major: <=41 -> 9, else 10.
required_tomcat_for_major() {
  local major="$1"
  if [[ "$major" =~ ^[0-9]+$ ]] && [ "$major" -le 41 ]; then
    echo 9
  else
    echo 10
  fi
}
```

- [ ] **Step 4: Run the Step 1 assertion script again to verify it passes**

Expected: every line `ok  : …` and a final `ALL_OK`.

- [ ] **Step 5: Commit**

```bash
git add bash-scripts-docker/d2-lib.sh
git commit -m "feat(lib): dhis2_major + required_tomcat_for_major helpers"
```

(Append to every commit body in this plan:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`)

---

## Task 2: version→Tomcat check in `d2-instance-create`

**Files:**
- Modify: `bash-scripts-docker/d2-instance-create`

Current relevant state: the defaults block contains `TOMCAT_VERSION="10"  # Default to Tomcat 10`; `getopts` is `"v:l:s:p:g:f:t:m:"`; and there is an abspath block ending with:

```bash
if [ -n "$DB_SEED" ]; then
  DB_SEED=$(abspath "$DB_SEED")
fi
```

followed (from the memory feature) by a `MEMORY=...`/`JAVA_MEM_OPTS=...` block, then `INSTANCE=$1`. `d2-lib.sh` is already sourced near the top. The template is selected later by `if [ "$TOMCAT_VERSION" = "9" ]; then …tomcat9… else …tomcat10… fi`.

- [ ] **Step 1: Default `TOMCAT_VERSION` to empty.** Change:

```bash
TOMCAT_VERSION="10"  # Default to Tomcat 10
```

to:

```bash
TOMCAT_VERSION=""  # empty = not specified; resolved below (auto from -v, else 10)
```

- [ ] **Step 2: Add the `-t` guard + version derivation.** Immediately after the abspath block shown above (before the `MEMORY=` block is fine; order between the two independent blocks does not matter), add:

```bash
# Validate an explicit -t and reconcile Tomcat with the DHIS2 version. Done
# here — before port selection, directory creation, or docker — so the error
# paths exit with no side effects.
if [ -n "$TOMCAT_VERSION" ] && [ "$TOMCAT_VERSION" != "9" ] && [ "$TOMCAT_VERSION" != "10" ]; then
  echo "Error: -t must be 9 or 10 (got '$TOMCAT_VERSION')" >&2
  exit 1
fi
if [ -n "$VERSION" ]; then
  DHIS_MAJOR=$(dhis2_major "$VERSION")
  REQ_TOMCAT=$(required_tomcat_for_major "$DHIS_MAJOR")
  if [ -z "$TOMCAT_VERSION" ]; then
    TOMCAT_VERSION="$REQ_TOMCAT"
    echo "Auto-selected Tomcat $TOMCAT_VERSION for DHIS2 $VERSION (major $DHIS_MAJOR)"
  elif [ "$TOMCAT_VERSION" != "$REQ_TOMCAT" ]; then
    echo "Error: DHIS2 $VERSION (major $DHIS_MAJOR) requires Tomcat $REQ_TOMCAT," >&2
    echo "       but -t $TOMCAT_VERSION was given. Omit -t to auto-select, or pass -t $REQ_TOMCAT." >&2
    exit 1
  fi
fi
# Fallback when nothing set it (no -v, or -l/-f WAR sources).
TOMCAT_VERSION="${TOMCAT_VERSION:-10}"
```

- [ ] **Step 3: Update `usage()`.** Replace the `-t` line:

```bash
  echo "  -t <version>    Tomcat version: 9 or 10 (default: 10)"
```

with:

```bash
  echo "  -t <version>    Tomcat 9 or 10 (usually omit: auto-selected from -v;"
  echo "                  DHIS2 <=2.41 -> 9, >=2.42 -> 10). Default 10 without -v."
```

- [ ] **Step 4: Syntax check**

Run: `bash -n bash-scripts-docker/d2-instance-create && echo SYNTAX_OK`
Expected: `SYNTAX_OK`.

- [ ] **Step 5: Verify the conflict + bad-`-t` error paths exit with NO side effects (no Docker needed).**

Run:
```bash
BASE=$(mktemp -d)
echo "--- conflict: -v 2.41 -t 10 (expect exit 1, no dir) ---"
DHIS2_BASE="$BASE" bash bash-scripts-docker/d2-instance-create -v 2.41 -t 10 zzz-conflict; echo "exit=$?"
echo "--- bad -t (expect exit 1) ---"
DHIS2_BASE="$BASE" bash bash-scripts-docker/d2-instance-create -v 2.42 -t 8 zzz-badt; echo "exit=$?"
echo "--- no instance dirs should have been created ---"
ls -A "$BASE" 2>/dev/null && echo "UNEXPECTED: something was created" || echo "clean (good)"
rm -rf "$BASE"
```
Expected: each create prints its `Error:` message and `exit=1`; the base dir is empty (`clean (good)`). (These invocations abort at the new check, before the port-selection/`docker` and `mkdir` steps.)

- [ ] **Step 6: Commit**

```bash
git add bash-scripts-docker/d2-instance-create
git commit -m "feat(create): auto-select Tomcat from -v version; reject conflicts"
```

---

## Task 3: documentation

**Files:**
- Modify: `README.md`, `docs/broker-api.md`, `docs/broker.md`, `skills/dhis2-instances/SKILL.md`

You must READ each file first to find exact anchors (line numbers drift). Make only the described edits.

- [ ] **Step 1: `README.md`.** Near the `d2-instance-create` examples (the "Create an instance" block), add a note line:

```bash
d2-instance-create -v 2.41 myinstance   # Tomcat auto-selected (2.41 -> Tomcat 9)
```

- [ ] **Step 2: `docs/broker-api.md` — update every "tomcat defaults to 10" occurrence.** First locate them:

Run: `grep -n -i "tomcat" docs/broker-api.md`

Then, in the `POST /instances` body example, change the `tomcat` comment to reflect auto-selection, e.g.:

```json
  "tomcat": "10",                // optional; "9"/"10". Auto-selected from version if omitted
```

In that endpoint's validation list, add:

```markdown
- `tomcat` is auto-selected from `version` when omitted (DHIS2 ≤ 2.41 → `9`,
  ≥ 2.42 → `10`). Passing a `tomcat` that conflicts with `version` fails the job.
```

And in the §7 "For creation" note (the "Default `tomcat` to `10`…" bullet found by the grep), reword to:

```markdown
- Leave `tomcat` unset when you pass a `version` — the broker auto-selects the
  compatible Tomcat. Only set it for the no-version (empty-instance) case.
```

- [ ] **Step 3: `docs/broker.md` — create-row note.** In the API endpoint table `POST /instances` row, the body already lists `"tomcat"?`. Immediately after the table (or in the create prose), add a sentence:

```markdown
`tomcat` is optional and auto-selected from `version` (DHIS2 ≤ 2.41 → Tomcat 9,
≥ 2.42 → Tomcat 10); a `tomcat` that conflicts with `version` fails the job.
```

- [ ] **Step 4: `skills/dhis2-instances/SKILL.md` — agent guidance.** In the "What you can and cannot do" list (or near the create examples), add a bullet:

```markdown
- **Don't set `tomcat` when you pass a `version`** — the broker auto-selects the
  compatible Tomcat (DHIS2 ≤ 2.41 needs Tomcat 9, ≥ 2.42 needs Tomcat 10). A
  conflicting `tomcat` makes the create job fail.
```

- [ ] **Step 5: Commit**

```bash
git add README.md docs/broker-api.md docs/broker.md skills/dhis2-instances/SKILL.md
git commit -m "docs: document Tomcat auto-selection from DHIS2 version"
```

---

## Task 4: live end-to-end verification (optional; needs Docker)

Confirms the auto-selected Tomcat actually produces a working older instance.
Run against a fresh broker on an alternate port, or via the CLI directly.

**Files:** none (verification only).

- [ ] **Step 1: Create a 2.41 instance with NO `-t` and confirm Tomcat 9 + it serves.**

```bash
# CLI (downloads a WAR, boots DHIS2 — several minutes):
d2-instance-create -v 2.41 agent-tc-check
# assert the container image is Tomcat 9:
grep 'image: tomcat' "$DHIS2_BASE/agent-tc-check/docker-compose.yml"   # expect tomcat:9.0-jdk17
docker inspect -f '{{.Config.Image}}' agent-tc-check-tomcat-1           # expect tomcat:9.0-jdk17
# once booted, DHIS2 should answer (not 404):
curl -s -o /dev/null -w '%{http_code}\n' -u admin:district \
  "http://localhost:$(grep -oE '[0-9]+:8080' "$DHIS2_BASE/agent-tc-check/docker-compose.yml" | cut -d: -f1)/api/system/info"
# expect 200 (after boot). Then clean up:
d2-instance-delete agent-tc-check
```
Expected: image is `tomcat:9.0-jdk17`; `/api/system/info` returns `200` once booted.

- [ ] **Step 2: Commit (only if Step 1 surfaced a fix)** — otherwise nothing to commit.

---

## Self-review notes

- **Spec coverage:** helpers + boundary (Task 1); default-empty + `-t` guard + auto-select/abort, side-effect-free early placement (Task 2); docs across all four surfaces incl. grep-all-occurrences (Task 3); testing — inline helper assertions + `bash -n` + no-Docker error path (Tasks 1–2), optional live (Task 4). Broker needs no change (spec §3) — no task, by design. Upgrade path / `-l`/`-f` / existing instances explicitly out of scope.
- **Naming consistency:** `dhis2_major`, `required_tomcat_for_major`, `TOMCAT_VERSION`, `VERSION`, `REQ_TOMCAT`, `DHIS_MAJOR` used identically across tasks; boundary (≤41→9, ≥42→10) stated the same everywhere.
- **Ordering:** the new check sits after option parsing / abspath and before port selection, `mkdir`, and `docker compose up`, so both error paths (Task 2 Step 5) leave no side effects.
