# Honor explicit ports + collision-free auto-select — design

Two related host-port problems on instance creation:

1. **The broker silently ignores `http_port`/`pg_port`.** `d2-broker`'s
   `create_instance` never reads those body fields and never passes `-p`/`-g`
   to `d2-instance-create`, so an agent that requested specific ports got the
   auto-selected ones instead ("accepted but ignored").
2. **Auto-select re-hands-out reserved ports.** `d2-instance-create` picks a
   free port by scanning only *currently bound* ports (`docker ps` +
   `is_port_in_use`). A port written into a **stopped** instance's
   `docker-compose.yml` is not bound, so new instances grab it too — the root
   cause of the recurring `9010`/`5433` collisions across `acdc`,
   `agent-emis-ng`, `agent-asc-ind`, `agent-ws-41-sl`.

This change (a) makes the broker honor explicit ports and (b) makes auto-select
avoid ports reserved by *any* instance, running or stopped.

## Decisions (resolved during brainstorming)

- **Scope:** do both — expose ports in the broker AND fix auto-select.
- **Explicit port already taken:** reject up front with a clear error rather
  than letting `docker` fail cryptically at `up` time.
- **Field validation is single-sourced sensibly:** the broker validates port
  *format* (integer range → `400`); the *availability* check lives in
  `d2-instance-create` (so it also covers CLI use), surfacing a taken explicit
  port as an early failed job with a clear message.
- **Scope of the port fields:** allowed for both admin and agent tokens (ports
  are configuration, like `tomcat`/`memory`; a bad/taken port just fails).
- **Unknown body fields:** NOT rejected. This change adds the two real fields
  only; making the broker reject unrecognized fields is a separate,
  contract-affecting decision and is out of scope.

## 1. Shared helpers in `bash-scripts-docker/d2-lib.sh`

```bash
# Host ports already mapped in any instance's compose (both the tomcat 8080 and
# db 5432 mappings). One port per line. Empty (not an error) if none match.
configured_ports() {
  local base="${DHIS2_BASE:-}"
  [ -n "$base" ] || return 0
  grep -hoE '[0-9]+:(8080|5432)' "$base"/*/docker-compose.yml 2>/dev/null \
    | cut -d: -f1 || true
}

# True (exit 0) if a host port is free to claim: not reserved by an instance
# compose, not published by a running container, not held by a host listener.
port_available() {
  local port="$1"
  if configured_ports | grep -qx "$port"; then return 1; fi
  if docker ps --format '{{.Ports}}' 2>/dev/null | grep -q ":$port->"; then return 1; fi
  if is_port_in_use "$port"; then return 1; fi
  return 0
}
```

`is_port_in_use` already exists in `d2-lib.sh`. The `grep … || true` and
`2>/dev/null` keep both helpers safe under `set -euo pipefail` when there are
no matches / no compose files. `configured_ports` reads every instance's
compose on each call; in the auto-select loop that is a few dozen small greps
per create — acceptable for a non-hot-path operation.

## 2. `bash-scripts-docker/d2-instance-create`

The instance being created has no directory yet, so its (future) ports are not
in `configured_ports` — no self-exclusion needed.

**Auto-select (collision-free).** Replace the inline checks in both loops with
`port_available`. The HTTP loop becomes:

```bash
if [ -z "$HTTP_PORT" ]; then
  echo "Auto-selecting HTTP port..."
  for port in $(seq 9010 9050); do
    if port_available "$port"; then HTTP_PORT=$port; echo "Selected HTTP port: $HTTP_PORT"; break; fi
  done
  if [ -z "$HTTP_PORT" ]; then
    echo "Error: No available HTTP ports found in range 9010-9050"; exit 1
  fi
else
  if ! port_available "$HTTP_PORT"; then
    echo "Error: HTTP port $HTTP_PORT is already in use or reserved by another instance" >&2
    exit 1
  fi
fi
```

The PG block is the analogous change (range `5433 5999`, message "PostgreSQL
port …"). Both blocks already run before `mkdir`/`docker compose up`, so the
new explicit-port error path has no side effects.

## 3. `bash-scripts-docker/d2-broker`

- **`valid_port(n)`** — a pure helper (unit-tested): returns `True` iff `n` is
  an integer with `1024 <= n <= 65535`.

  ```python
  def valid_port(n):
      try:
          p = int(n)
      except (TypeError, ValueError):
          return False
      return 1024 <= p <= 65535
  ```

- **`create_instance`** — after the existing `tomcat`/`memory` handling and
  before `argv.append(name)`, add:

  ```python
  for field, flag in (("http_port", "-p"), ("pg_port", "-g")):
      val = body.get(field)
      if val is not None:
          if not valid_port(val):
              raise ApiError(400, f"{field} must be an integer 1024-65535")
          argv += [flag, str(int(val))]
  ```

  Availability is enforced downstream by `d2-instance-create` (a taken port →
  the create job fails early with the clear message). The broker validates only
  format. Both scopes may pass the fields.

## 4. Out of scope

- No change to reset/start/stop/upgrade/memory/backup endpoints.
- The broker does not reject unknown body fields.
- Existing instances are not re-numbered or migrated (their ports remain as
  written; deconflicting them stays a manual/edit-the-compose task).

## 5. Documentation

- `docs/broker-api.md`: add `http_port` / `pg_port` to the `POST /instances`
  body example and validation list (integer 1024–65535; a taken port fails the
  job; omit to auto-select a free, non-colliding port).
- `docs/broker.md`: add the two optional fields to the `POST /instances` body
  in the endpoint table; note auto-select now avoids ports reserved by other
  instances (running or stopped).
- `skills/dhis2-instances/SKILL.md`: agents normally use `devnet_url` and don't
  need host ports; document `http_port`/`pg_port` as available but rarely
  needed.
- `d2-instance-create` `usage()`: `-p`/`-g` are already listed; no wording
  change required (behavior is now "validated + collision-aware").

## 6. Testing

**Mechanism:** inline shell assertions (repo convention) for the bash helpers;
the broker's Python `unittest` suite (`test_d2_broker.py`) for `valid_port`.

- **`valid_port` (Python unit test):** `8080→True`, `1024→True`, `65535→True`,
  `1023→False`, `70000→False`, `"abc"→False`, `None→False`, `"9010"→True`.
- **`configured_ports` (no Docker):** point `DHIS2_BASE` at a temp dir holding
  two fake `*/docker-compose.yml` files with `"9010:8080"` and `"5433:5432"`
  mappings; assert the function lists `9010` and `5433`; assert it prints
  nothing (and exit 0) for an empty base.
- **`bash -n`** on `d2-lib.sh` and `d2-instance-create`.
- **Broker import + full unit suite** still pass.
- **Live (optional, Docker):** with a stopped instance whose compose reserves
  `9010`, run an auto-select create and confirm it picks a port ≠ `9010`; run a
  create with an explicit taken `-p 9010` / `http_port:9010` and confirm it
  fails fast with the clear message (not the docker allocation error).
