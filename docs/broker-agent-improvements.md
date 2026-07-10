# d2-broker improvements from agent-review feedback

Suggestions surfaced while agents used the broker for multi-version DHIS2 app
reviews (2026-07). Same contract style as [proposed-broker-endpoints.md](./proposed-broker-endpoints.md).
Broker source: `dhis2-docker-tools/bash-scripts-docker/d2-broker`.

## Done

- **Refuse in-place upgrades across the Tomcat 9→10 boundary.** `POST
  /instances/<name>/upgrade` with a `version` that needs a different Tomcat
  major than the instance runs (≤41 → Tomcat 9, ≥42 → Tomcat 10) now returns
  `400` with guidance to create fresh at the target version with the older
  seed. Previously it deployed the new WAR onto the old Tomcat, booting to an
  all-404 instance with no error. (`required_tomcat_major()` +
  guard in `upgrade_instance`.) A *true* in-place swap — recreating the Tomcat
  container on the new image while preserving DB/volumes — remains unimplemented
  and is the harder follow-up if cross-boundary upgrade is ever needed.

## Proposed

### 1. Instance readiness status on `GET /instances`

**Problem:** during boot, `/api/system/info` walks through
connection-refused → 404 (WAR deploying) → 401/200, and agents have to poll and
guess which phase they're in. `list_instances()` already derives
`running`/`stopped`/`partial` from container state, but "container running" ≠
"DHIS2 ready".

**Proposed:** add a `readiness` field to each element, orthogonal to the
container `status`:

- `deploying` — Tomcat up, `/api` not yet answering (WAR still unpacking)
- `migrating` — `/api` answering but Flyway migrations in progress
- `ready` — `/api/system/info` returns 200 with a resolved version
- `null` — not determinable / instance stopped

Cheapest implementation: probe `http://<tomcat>:8080/api/system/info` from the
broker when `full=1` is requested (it already does per-instance work there), and
map the response (refused/404/401/200) to the states above.

### 2. Per-seed credential metadata on `GET /seeds`

**Problem:** some seeds ship with `admin` disabled or a non-default password
(e.g. the Laos v41 demo). Discovering this costs a failed-login-cache restart
cycle. The `local_admin`/`district` superuser the broker injects into every
instance already mitigates this — agents should just use it — but naming the
quirk up front would still save confusion.

**Proposed:** let a seed carry an optional sidecar (`<seed>.meta.json`) that
`GET /seeds` surfaces as a `credentials` hint, e.g.
`{"admin_disabled": true, "note": "use local_admin/district"}`. Purely
advisory; no behavior change. Alternatively, keep it out of the API and rely on
`local_admin` being universal (documented in the `dhis2-instances` skill).
