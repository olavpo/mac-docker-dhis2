# Prevent Tomcat/DHIS2 version mismatch at instance creation — design

Adds a compatibility check to `d2-instance-create` so a DHIS2 version is never
deployed onto an incompatible Tomcat. DHIS2 ≤ 2.41 uses the `javax.servlet` API
(Tomcat 9); DHIS2 ≥ 2.42 uses `jakarta.servlet` (Tomcat 10). The repo currently
defaults to Tomcat 10, so creating an older instance (e.g. 2.41) without
`-t 9` yields a container where Tomcat starts but the DHIS2 webapp never
initializes — every URL returns 404.

## Decisions (resolved during brainstorming)

- **Behavior:** when `-v <version>` is given, auto-select the correct Tomcat if
  `-t` is omitted; if `-t` is given and conflicts with the version, abort with a
  clear error (no auto-override).
- **Boundary:** DHIS2 major ≤ 41 → Tomcat 9; ≥ 42 → Tomcat 10.
- **Scope:** the check applies only when `-v` provides a version. `-l` (WAR URL)
  and `-f` (WAR file) are unchanged — the version can't be read up front.
- **Single source of truth:** the boundary rule lives only in `d2-lib.sh`. The
  broker gains the behavior for free (it calls `d2-instance-create`); it does
  not duplicate the 41/42 constant.

## 1. Helpers in `bash-scripts-docker/d2-lib.sh`

Two small, pure, network-free functions (reused by the check; unit-testable by
sourcing the lib):

```bash
# DHIS2 major from a version string: 42, 2.42, 2.42.4, 41.4 -> 42 / 41.
dhis2_major() {
  local v="$1"
  if [[ "$v" =~ ^2\.([0-9]+) ]]; then
    echo "${BASH_REMATCH[1]}"        # strip leading "2." (the 2.x line)
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

`dhis2_major` outputs for representative inputs: `42→42`, `2.42→42`,
`2.42.4→42`, `41→41`, `41.4→41`, `2.41.3→41`. A non-numeric/garbage major flows
to `required_tomcat_for_major`, which returns `10` (the modern default) rather
than crashing under `set -euo pipefail`.

## 2. The check in `bash-scripts-docker/d2-instance-create`

- Change the default `TOMCAT_VERSION="10"` to `TOMCAT_VERSION=""` (empty means
  "not specified on the command line").
- Add a `-t` value guard: if `TOMCAT_VERSION` is non-empty and not `9`/`10`,
  abort (`Error: -t must be 9 or 10`). This closes a pre-existing footgun where
  an invalid `-t` silently fell through to the Tomcat 10 template.
- Insert the derivation/validation **early — right after option parsing and the
  abspath block, before any port selection, directory creation, or
  `docker compose up`** — so the error path exits with no side effects:

```bash
# Tomcat/DHIS2 compatibility (only when a version is known via -v).
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
# Fallback when nothing set it (no -v, or -l/-f WAR sources): keep the default.
TOMCAT_VERSION="${TOMCAT_VERSION:-10}"
```

The `-t` value guard must run before this block (or be folded into it) so a bad
`-t` is caught regardless of whether `-v` is present.

Behavior summary:

| Invocation | Result |
|---|---|
| `-v 2.41` (no `-t`) | Tomcat 9 (auto), note printed |
| `-v 2.42` (no `-t`) | Tomcat 10 (auto) |
| `-v 2.41 -t 9` | Tomcat 9 (agrees) |
| `-v 2.41 -t 10` | **Error, exit 1**, no side effects |
| `-v 2.42 -t 9` | **Error, exit 1** |
| no `-v` (empty instance) | Tomcat 10 (default) or explicit `-t` |
| `-l <url>` / `-f <file>` | unchanged (default 10 or explicit `-t`) |
| `-t 8` (any) | **Error, exit 1** (`-t must be 9 or 10`) |

## 3. Broker (`bash-scripts-docker/d2-broker`) — no code change

The broker builds a `d2-instance-create` argv and only passes `-t` when the
request body includes `tomcat`. Therefore:

- `POST /instances {"version":"2.41"}` (no `tomcat`) → the script auto-selects
  Tomcat 9. The common path is fixed with zero broker changes.
- `POST /instances {"version":"2.41","tomcat":"10"}` (explicit conflict) → the
  script exits non-zero, so the job ends as `failed` with the error text in the
  job log. This is an accepted, rare case; the boundary constant stays
  single-sourced in `d2-lib.sh`.

The broker's existing upfront `tomcat` validation (`"9"`/`"10"`) is unchanged.

## 4. Out of scope

- **Upgrade path.** A cross-boundary upgrade (2.41 → 2.42) also needs a Tomcat
  switch; `POST /instances/<name>/upgrade` already refuses Tomcat changes. That
  pre-existing limitation is separate and not addressed here.
- **`-l`/`-f` WAR sources.** No version is available to check.
- **Existing instances.** No migration or back-fix of already-created instances.

## 5. Documentation

- `d2-instance-create` `usage()`: note that `-t` auto-selects from `-v` and is
  usually unnecessary; `-t` must be `9` or `10`.
- `README.md`: mention that `-v` picks the right Tomcat automatically.
- `docs/broker-api.md`: on `POST /instances`, note `tomcat` is optional and is
  auto-selected from `version`; a conflicting `tomcat` fails the job. When
  editing, `grep` for every occurrence of the current "`tomcat` … default
  `10`" framing (there are at least two — the `POST /instances` body comment and
  the §7 "For creation" note) so the doc isn't left self-contradictory.
- `docs/broker.md`: same note on the create row.
- `skills/dhis2-instances/SKILL.md`: tell agents not to set `tomcat` alongside
  `version` — the broker picks the compatible Tomcat.

## 6. Testing

**Mechanism:** the repo has no bash test harness (only the broker's Python
`unittest` suite in `test_d2_broker.py`). Following the established convention
for the shell tools, these are **inline verification commands run during the
plan** (`bash -n` plus `bash -c 'source d2-lib.sh; …'` assertions) — not a new
bash test framework.

- **Bash helper checks (no Docker)** — source `d2-lib.sh` and assert:
  - `dhis2_major`: `42→42`, `2.42→42`, `2.42.4→42`, `41→41`, `41.4→41`.
  - `required_tomcat_for_major`: `41→9`, `40→9`, `42→10`, `43→10`.
- **`bash -n`** on the edited `d2-instance-create` and `d2-lib.sh`.
- **Error-path (no Docker, no side effects):** `d2-instance-create -v 2.41 -t 10 tmpname`
  exits non-zero and does NOT create `$DHIS2_BASE/tmpname/`; likewise `-t 8`.
- **Auto-select rendering (no full boot):** confirm that with `-v 2.41` and no
  `-t`, the selected template is the Tomcat 9 one (assert the generated
  `docker-compose.yml` uses `tomcat:9.0-jdk17`). This can be checked by reading
  the produced compose file. Because a real create downloads a WAR and boots
  DHIS2, a full end-to-end create is optional; the compose-image assertion is
  the primary evidence that the correct Tomcat was chosen.
- **Optional live check:** create a real `2.41` instance with no `-t`, confirm
  the container image is `tomcat:9.0-jdk17` and DHIS2 answers on `/api/system/info`.
