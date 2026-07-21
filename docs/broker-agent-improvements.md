# d2-broker status & open improvements

Running status of broker changes surfaced by agent-review feedback and client
needs. The living API contract is [broker-api.md](./broker-api.md) /
[broker.md](./broker.md); this file tracks what shipped and what's still open.
Broker source: `dhis2-docker-tools/bash-scripts-docker/d2-broker`.

## Done

- **Boot-gate: booting jobs hold until the API is ready** (2026-07-21). After a
  `create` / `reset` / `start` / `upgrade` / `memory` job's steps succeed, the
  worker blocks (`wait_for_boot()`) until the instance reports `ready` or
  `D2_BROKER_BOOT_WAIT` seconds (default 1800) elapse, *before* the job
  finishes. Because the single worker is globally serialized, this serializes
  app boot: the next booting job can't start its heavy Flyway migration until
  this instance is up, ending the mutual starvation of two concurrent boots.
  Best-effort — a cap timeout releases the worker with a log line and does not
  fail the job. Side benefit: `upgrade`'s `dhis2_major_version` is now read
  post-migration, so it's accurate. Depends on the readiness fix below. (Was
  proposed #4.)

- **Readiness probe authenticates; `ready` reflects real usability**
  (field 2026-07-16, auth fix 2026-07-21). `readiness` on `GET
  /instances?full=1`: `deploying` (no HTTP answer on `/api`) / `migrating`
  (HTTP answers, no version yet) / `ready` / `null` (not running). The probe
  (`instance_readiness()`) now sends HTTP Basic auth for the guaranteed
  `local_admin`/`district` superuser: anonymous `/api/system/info` omits the
  `version` field, so the original unauthenticated probe sat at `migrating`
  long after the API was usable (often never flipping). Authenticated, `ready`
  trips when the API actually serves metadata (a 401/403 still counts —
  the security layer is up). Note `ready` = metadata-usable, **not** that
  analytics / resource tables are built. (Auth fix was proposed #8.)

- **D2Manager endpoints shipped** — consolidates the former
  `proposed-broker-endpoints.md` (removed; superseded by `broker-api.md`). The
  "stopped instances vanish from `GET /instances`" bug is fixed:
  `list_instances()` enumerates by **instance directory** and derives status by
  probing containers (`running` / `partial` / `stopped`), so `stop` can keep
  using `docker compose down` while stopped instances still list. And
  `POST /instances/<name>/backup` (admin-only) and
  `POST /instances/<name>/upgrade` are both implemented and documented.

- **Refuse in-place upgrades across the Tomcat 9→10 boundary.** `POST
  /instances/<name>/upgrade` with a `version` that needs a different Tomcat
  major than the instance runs (≤41 → Tomcat 9, ≥42 → Tomcat 10) now returns
  `400` with guidance to create fresh at the target version with the older
  seed. Previously it deployed the new WAR onto the old Tomcat, booting to an
  all-404 instance with no error. (`required_tomcat_major()` +
  guard in `upgrade_instance`.) A *true* in-place swap — recreating the Tomcat
  container on the new image while preserving DB/volumes — remains unimplemented
  and is the harder follow-up if cross-boundary upgrade is ever needed.

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

> **Review 2026-07-21:** #4 (concurrent-boot gate) and #8 (readiness accuracy)
> are now implemented — see Done. Of the rest, **#6** is a useful host-side
> seed-prep task; #2 and #5 are largely obsolete; #3 and #7 are low-value /
> edge. (Numbering keeps the original gaps for stability.)

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

**Status (review 2026-07):** largely obsolete. The guaranteed
`local_admin`/`district` superuser — now ensured on create *and* restore —
already solves the underlying "can't log in" problem. Recommend the documented
alternative (skill: "use local_admin/district") and dropping the API surface.

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

**Status (review 2026-07):** low marginal value. The broker has no autonomous
stop path — it only stops on an explicit `POST /stop`, where the reason is
just "stopped via API". The genuinely confusing cases (host resource
management, manual `docker stop`) are exactly the ones the broker can't
annotate. The skill-doc guidance captures nearly all the value.

### 5. Broker-side warm-up folded into the readiness probe

**Problem:** on 2.41 and earlier, legacy Struts endpoints 500 until any
Struts page is loaded once (`/dhis-web-commons/security/login.action`).
Agents re-implement the warm-up.

**Proposed:** when the readiness probe first sees `ready` on an instance
with DHIS2 major ≤ 41, fire one GET at the Struts login page. One line of
state per instance ("warmed") in `_broker/meta/`.

**Status (review 2026-07):** low and shrinking value (≤ 2.41 only; ages out as
the fleet moves to 42/43). If ever done, keep it **out** of the readiness
probe — `GET /instances?full=1` must stay a pure read (a probe that fires
state-changing warm-up GETs would repeat on every poll). Make it an explicit
create-time step instead.

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

**Conflict with disable-jobs-on-restore (shipped 2026-07-20):** every restore
now sets `jobconfiguration.enabled = false` on all rows, so a fixture seed
shipping a *running* job/queue would arrive with that job **disabled** — the
fixture is neutered on the way in. A fixture approach must therefore configure
jobs *post*-restore (via API), carve a documented exception into
`disable_scheduled_jobs`, or have the reviewer re-enable the specific fixture
jobs after reset.
