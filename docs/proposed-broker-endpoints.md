# d2-broker changes needed by D2Manager

Changes the **D2Manager** menu bar app needs from the `d2-broker` HTTP API
([broker-api.md](./broker-api.md)): one **bug fix** to an existing endpoint, and
two **new endpoints**. Written in the same contract style as the existing API so
they can be implemented directly. Broker source:
`dhis2-docker-tools/bash-scripts-docker/d2-broker`.

The new actions ship as **disabled placeholders** in each instance's ⋯ menu
(Backup DB, Deploy WAR…) until the endpoints exist.

---

## 0. Bug: stopped instances disappear from `GET /instances`

**Symptom:** stopping an instance removes it from the list entirely instead of
showing it as `stopped`. It only reappears when started again (e.g. from the CLI).

**Root cause:** `stop` runs `d2-shutdown` → `docker compose down`, which *removes*
the instance's containers. But `list_instances()` enumerates instances from
`docker ps -a` (matching `<name>-tomcat-1` / `<name>-db-1`). With the containers
gone, the instance is no longer enumerated — even though its directory under
`$DHIS2_BASE/<name>/` still exists (which is what `require_instance` treats as
"exists"). So the documented `stopped` status is only reachable for *exited*
containers (e.g. `compose stop`), never via the `stop` endpoint, which uses `down`.

**Recommended fix:** make `list_instances()` enumerate by **instance directory**
(`$DHIS2_BASE/*/` — the same source of truth `require_instance` already uses) and
derive status by probing containers:

- tomcat + db both running → `running`
- exactly one running → `partial`
- containers exited or absent → `stopped`

This keeps `stop = docker compose down` (frees container resources) while still
listing the instance as `stopped`. `http_port` / `pg_port` / `devnet_*` are
`null` when no container exists — the app already handles null ports.

**Alternative:** change `stop` to `docker compose stop` (containers remain in the
exited state) so `docker ps -a` keeps listing them. Simpler, but leaves
containers around and changes the documented stop semantics.

**App side:** no change — the app renders exactly what `GET /instances` returns,
and cannot see directory-only instances over the loopback API.

---

## What already works today (no broker change needed)

- **Restore DB from a backup.** `POST /instances/<name>/reset` already accepts a
  seed under `_backups/` (prefix `backups/…`) for the **admin** token, and
  `GET /seeds` already returns those backups to admin. So "restore from a
  previous backup" is just the existing Restore DB… flow with a `backups/…`
  seed — the app's seed picker already lists them. **No new endpoint required;**
  the only gap on the backup side is *creating* backups (below).

---

## 1. Create a database backup — `POST /instances/<name>/backup`

**Motivation / app affordance:** the ⋯ → **Backup DB** action; also the natural
"safety net before an upgrade or restore."

**Request body (all optional):**

```json
{ "label": "pre-upgrade" }     // optional tag folded into the filename
```

**Behaviour:** `pg_dump` the instance's database to
`$DHIS2_BASE/_backups/<name>/<timestamp>[-<label>].sql.gz`. Returns **202 + job**
(same job model as the rest of the API).

**Job `result` on success:**

```json
{
  "path": "backups/<name>/2026-06-14T09-14-01.sql.gz",
  "source": "backups",
  "size_bytes": 928374829,
  "modified": "2026-06-14T09:14:01+00:00"
}
```

i.e. the same shape as a `GET /seeds` element, so the UI can show it and offer
it immediately as a restore source.

**Validation / scope:**

- **Admin only** (backups are already admin-scoped in `GET /seeds`); agent token → `403`.
- `label` (if present) must match `^[a-z0-9][a-z0-9_-]{0,39}$`.
- The DB must be reachable. If the instance is `stopped`, the broker should
  either start the DB container for the dump or fail with a clear message
  (decision below).

**Open questions:**

- Backup a stopped instance? (Start DB container transiently vs. require running.)
- Retention/pruning of old backups, or leave to the user?

---

## 2. Upgrade / redeploy an instance — `POST /instances/<name>/upgrade`

**Motivation / app affordance:** the ⋯ → **Deploy WAR…** action *and* a
version-upgrade flow. These are the same operation — swap the running WAR — so
one endpoint covers both: a version bump (resolve a release WAR) or a specific
WAR (custom build / war_url / war_file).

**Request body (one of `version` / `war_url` / `war_file` required):**

```json
{
  "version":  "2.42.4",          // resolve latest stable for "42"/"2.42"
  "war_url":  "https://…/dhis.war",   // admin only
  "war_file": "/abs/path.war",        // admin only
  "tomcat":   "10",              // optional; change servlet container if needed
  "backup_first": true           // optional; take a backup before swapping (default true)
}
```

**Behaviour:** preserving the DB and volumes — stop tomcat, replace the WAR,
start tomcat, and let Flyway run the schema migrations on boot. Returns
**202 + job**; `result` is the updated instance element (with the new
`dhis2_major_version`), same shape as `GET /instances`.

**Validation / scope:**

- `version` matches `^[0-9][0-9.]{0,15}$`; major-only resolves at job-run time
  (same rule as create).
- `war_url` / `war_file` are **admin only** (same as create) → agent token `403`.
- `tomcat` is `"9"` or `"10"`.

**Open questions / risks (worth capturing for whoever implements it):**

- **Pre-upgrade backup:** default `backup_first: true` (reuse endpoint #1) so a
  failed migration is recoverable. Surface the backup path in the job log.
- **DHIS2 / Java / Tomcat compatibility:** some DHIS2 majors require a specific
  Tomcat/Java; the broker may need to bump the container image, not just the WAR.
- **Cross-major jumps & downgrades:** DHIS2 generally does not support skipping
  majors or downgrading. The broker should reject/​warn on unsupported jumps
  rather than corrupt the DB. Define the allowed transition policy.
- **Long migrations:** Flyway on a large DB can take minutes — fits the existing
  poll-the-job model; `log_tail` should stream migration progress.

---

## 3. Cross-cutting (consistency with the current API)

- **Job model:** all three are state-changing → **202 + job**, polled via
  `GET /jobs/<id>`, with `result` on success matching a `GET /instances` (for
  upgrade) or `GET /seeds` (for backup) element, so the UI refreshes without a
  follow-up call. Matches the existing create/reset/start/stop/delete contract.
- **Global serialization:** they run on the same single worker; a second op on
  an instance with an active job → `409` (unchanged).
- **Errors:** keep the `{ "error": "…" }` body with user-facing messages.

---

## App wiring once these land

- **Backup DB** placeholder → calls `POST /instances/<name>/backup`, shows the
  job banner, and the new backup appears in the Restore DB… seed picker.
- **Deploy WAR… / Upgrade** placeholder → a small form (version free-text or
  war_url/war_file, tomcat, "backup first" toggle) posting to
  `POST /instances/<name>/upgrade`.
- **Restore from backup** already works via the existing Restore DB… picker (it
  lists `backups/…` seeds for admin) — no app change needed beyond what exists.

(These three are also the items currently rendered as disabled menu entries in
`InstanceRowView`'s ⋯ menu, under the "Requires broker support" section.)
