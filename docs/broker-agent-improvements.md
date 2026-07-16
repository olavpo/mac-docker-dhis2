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

- **Instance readiness on `GET /instances?full=1`** (2026-07-16). `readiness`
  field per instance: `deploying` (no HTTP answer on `/api`) / `migrating`
  (HTTP answers, `system/info` has no version yet) / `ready` (200 with
  version, or 401/403) / `null` (not running). Probed from the broker on the
  host-published port with a 3 s timeout (`instance_readiness()`). Only on
  `full=1`, same as `dhis2_major_version`.

- **`created_at` + `label` on instances** (2026-07-16). `POST /instances`
  accepts an optional free-text `label` (≤ 100 printable chars); the broker
  stamps `created_at` at create-accept time. Both are echoed on every
  `GET /instances` element (`null` for pre-existing instances). Stored in
  `_broker/meta/<name>.json`; lets concurrent agent sessions recognise their
  own instances instead of treating every `agent-*` leftover as stale.

- **`dhis2_version` on `GET /seeds`** (2026-07-16). The `_vNN` filename token
  the broker already parsed internally (`seed_major()`) is now exposed per
  seed element (`null` when the filename has no token), so version planning
  doesn't need filename archaeology.

## Proposed

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

### 3. `stopped_reason` / event on host-initiated stops

**Problem (2026-07-14 session):** a running agent instance was stopped
mid-session (host-side resource management or manual intervention), silently
breaking a dev server proxying to it — the failure surfaced only as a DNS
error minutes later. The instance just shows `status: stopped` with no hint
of why or when.

**Proposed:** when the broker itself stops an instance, record
`stopped_reason` + timestamp in `_broker/meta/<name>.json` (the file now
exists) and surface it on `GET /instances`. Stops done directly via Docker
can't carry a reason — documenting "instances may be found stopped; `start`
is cheap; check status before long test runs" in the skill covers that half.

### 4. Fail fast / queue when a create would boot concurrently

**Problem:** two DHIS2 instances *booting* at once starve each other (20+
min with nothing on `/api`); agents discover the constraint the hard way.
The jobs queue already serializes create/reset *jobs*, but a create job
finishes when containers are up — DHIS2 keeps booting long after, so a
second create can start while the first is still migrating.

**Proposed:** before running a create/reset job, check for another instance
with `readiness` in (`deploying`, `migrating`); either delay the job start
(status `queued`, log line "waiting for <name> to finish booting") or —
simpler — return an advisory field in the job response. Needs care: an
instance stuck un-ready forever must not deadlock the queue (cap the wait).

### 5. Broker-side warm-up folded into the readiness probe

**Problem:** on 2.41 and earlier, legacy Struts endpoints 500 until any
Struts page is loaded once (`/dhis-web-commons/security/login.action`).
Agents re-implement the warm-up.

**Proposed:** when the readiness probe first sees `ready` on an instance
with DHIS2 major ≤ 41, fire one GET at the Struts login page. One line of
state per instance ("warmed") in `_broker/meta/`.

### 6. Pre-migrated Laos seeds for v42/v43 (seed prep, not code)

The Laos HMIS demo seed exists only as `_v41`; using it on 2.42/2.43 costs a
10–25 min Flyway migration on first boot, which dominates multi-version
review passes. Boot it once per target major, let Flyway finish, `d2-db-backup`
the result into `_seeds` as `..._v42`/`..._v43`. (Blocked on backup being
admin-only: this is a host-side task, not something agents can do.)

### 7. Seeds with job/scheduler fixtures

Reviews of job-monitoring/scheduler apps need a running job and a scheduler
queue; demo seeds ship neither, so agents hand-build them
(3× `POST /api/jobConfigurations` + `POST /api/scheduler/queues/<name>`).
A seed variant with a long-running job + queue pre-configured — or a broker
"fixture" helper — would make this turnkey. Same class of gap: no seed has a
configured predictor.
