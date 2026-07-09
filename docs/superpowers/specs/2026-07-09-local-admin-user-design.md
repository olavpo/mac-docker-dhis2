# A known `local_admin` superuser on restore, stripped from backups — design

Give the agent a superuser account with known credentials on every instance,
regardless of what database was restored (restored real backups often have an
unknown or disabled `admin`). The account is added on restore, added on fresh
empty creates, and removed from backup dumps so the known-password superuser is
never persisted in shared/real backups.

- **Username:** `local_admin`  **Password:** `district` (bcrypt hash inserted
  directly; DHIS2 accepts `$2a$`/`$2b$` cost-10). Fixed DHIS2 uids for the user
  and its role so operations are idempotent and targetable.
- Loopback/test tooling only; the security trade-off (a known-password
  superuser on local test instances) is acceptable and is why it's stripped
  from backups.

## Decisions (resolved during brainstorming)

- **Grants:** a **dedicated Superuser role** with the `ALL` authority, plus the
  **root org unit(s)** as **data-capture** membership only. **No data-view**
  org units.
- **Empty (no-seed) creates:** add via the **same `add` SQL as restore**, run
  after the instance finishes booting. (The API was considered but rejected: it
  can't set the `district` password because DHIS2's password policy rejects it,
  whereas a hash inserted via SQL bypasses the policy. SQL also needs no restart
  — `local_admin` has never been looked up, so nothing is cached for it.)
- **Backup removal:** **reassign then delete** — repoint `local_admin`'s
  foreign-key references to `admin`, then delete it; **scrub** (disable +
  unusable password, keep the row) as a fallback when no `admin` user exists.
- **Where:** on restore (covers reset + create-with-seed) and empty creates.
  The broker needs no change — its reset/backup/create endpoints call these
  scripts.

## 1. Shared helper: `bash-scripts-docker/d2-local-admin`

`d2-local-admin <instance> <add|remove>` — runs SQL in the instance's DB via
`docker exec <db-container> psql -U dhis -d dhis2`. Resolves the container with
`resolve_db_container` (from `d2-lib.sh`); errors clearly if the DB isn't
running.

Fixed identifiers (constants in the script):
- user uid `LocalAdmn01`, username `local_admin`, password bcrypt-of-`district`.
- role uid `LocalAdmnR1`, role name `local_admin superuser`.
(Both are valid DHIS2 uids: 11 chars, leading letter. Exact literals may be
adjusted during implementation.)

### `add` (idempotent)
Run as one SQL transaction:
1. **Clean slate:** delete any existing `local_admin` (by username) — its
   `usermembership` and `userrolemembers` rows first, then the `userinfo` row —
   and the dedicated role by uid (`userroleauthorities` then `userrole`).
2. **Role:** insert `userrole` (fixed uid/name, `created`/`lastupdated` = now())
   and `userroleauthorities (userroleid, authority) = (…, 'ALL')`.
3. **User:** insert the `local_admin` `userinfo` row. To satisfy the many
   NOT-NULL columns robustly across versions, populate it by cloning an existing
   `userinfo` row's structure (any row — e.g. lowest `userinfoid`) and
   overriding the identity columns: new `userinfoid` (sequence), fixed `uid`,
   `username='local_admin'`, `password=<bcrypt district>`, `disabled=false`,
   `firstname='Local'`, `surname='Admin'`, and null out `code`/`email`/`openid`/
   two-factor/secret-type columns. (A `psql` DO/temp-table copy-then-override so
   we never enumerate the full column list.)
4. **Role membership:** `userrolemembers (userroleid, userid)`.
5. **Org units (data capture only):** `usermembership (userinfoid,
   organisationunitid)` for every root org unit (`organisationunit WHERE
   parentid IS NULL`). Inserts nothing when the DB has no org units (fine).

### `remove`
Run as one SQL transaction.
1. Resolve `local_admin`'s `userinfoid` (no-op if absent) and `admin`'s
   `userinfoid`.
2. **If `admin` exists — reassign then delete:** a `DO`/plpgsql block queries
   `information_schema` for every column with a foreign key referencing
   `userinfo(userinfoid)`, and for each runs `UPDATE <t> SET <col>=<admin_id>
   WHERE <col>=<local_admin_id>` (repointing created-by/last-updated-by/etc.).
   The loop **explicitly excludes the junction tables `usermembership` and
   `userrolemembers`** — those are `local_admin`'s own membership rows, deleted
   in the next step; reassigning them to `admin` would collide on their unique
   keys (`local_admin` is granted the same root org unit `admin` typically
   already has). Then delete, respecting FK order: `local_admin`'s
   `usermembership` and `userrolemembers` rows, then its `userinfo` row; and for
   the dedicated role, `userroleauthorities` **before** `userrole`.
3. **If `admin` does not exist — scrub:** set `local_admin.password` to a random
   unusable value and `disabled=true`; leave the row and role in place. (The
   backup then contains a disabled, unusable `local_admin` rather than a
   known-password one.)

Reassigning means objects the agent created as `local_admin` are re-attributed
to `admin` in the backup — acceptable for disposable test instances; noted so
it isn't surprising.

## 2. Restore wiring (`bash-scripts-docker/d2-db-restore`)

After a successful restore and **before** the existing Tomcat restart, call
`d2-local-admin <instance> add`. Because the restore has already stopped Tomcat,
the user-details cache is rebuilt on the restart that already happens — no extra
restart. If `add` fails, log a clear warning but do not fail the restore (the
restored DB is still valid); surface the warning in the restore output.

## 3. Backup wiring (`bash-scripts-docker/d2-db-backup`)

Wrap the dump: `d2-local-admin <instance> remove` → `pg_dump | gzip` →
`d2-local-admin <instance> add`, with a `trap` ensuring the re-`add` runs even
if the dump fails. The instance is running; delete-then-re-add restores the same
username/uid/password, so the cached session keeps working — no restart. Net
effect: the `.sql.gz` never contains an active `local_admin`.

## 4. Empty-create wiring (`bash-scripts-docker/d2-instance-create`)

Applies only when a `-v` version is deployed with **no** `-s` seed (an empty
DHIS2 that Flyway initializes on first boot). Create-with-seed already goes
through `d2-db-restore` (§2); no-version creates have no DHIS2 to add a user to.

After the instance boots, poll `GET /api/system/info` with `admin`/`district`
until it answers (bounded timeout — DHIS2 needs 1–5 min to run Flyway), which
confirms the schema exists, then run the same `d2-local-admin <instance> add`
SQL as the restore path. No Tomcat restart is needed: `local_admin` has never
been looked up, so nothing is cached for it and its first login loads fresh from
the DB. Strictly **best-effort** — any failure (boot timeout, SQL error) logs a
warning and does not fail the create; empty instances still have the known
`admin`/`district`.

(The DHIS2 API was considered for this path but rejected: `POST /api/users`
would reject the `district` password under DHIS2's password policy, whereas the
bcrypt hash inserted via SQL bypasses it.)

## 5. Broker — no change

`POST /instances/<name>/reset` → `d2-db-restore` (gets §2). `POST
/instances/<name>/backup` → `d2-db-backup` (gets §3). `POST /instances` with a
seed → `d2-db-restore`; empty → `d2-instance-create` (gets §4). No broker code
changes.

## 6. Out of scope

- No-version ("shell") creates (no DHIS2 running).
- Configurable username/password (fixed `local_admin`/`district`; can be made
  env-driven later).
- Pre-2.36 DHIS2 schemas (separate `usercredentials` table). The tool targets
  current versions where credentials live on `userinfo`.

## 7. Version-sensitivity / validation

Exact table and column names (`userinfo`, `userrole`, `userroleauthorities`,
`userrolemembers`, `usermembership`, `organisationunit`) and the set of NOT-NULL
`userinfo` columns are validated against a live current instance during
implementation. The clone-a-row technique (§1.3) avoids hard-coding the column
list. If any object differs on the target version, the live test (below) catches
it.

## 8. Testing

- **`d2-local-admin` on a live restored instance:** run `add`; assert
  `GET /api/me` with `local_admin:district` (Basic auth) returns 200 and the
  user has `ALL` authority; assert data-capture org unit(s) assigned. Run `add`
  again → still one `local_admin` (idempotent). Run `remove` → `local_admin`
  gone (or scrubbed when `admin` absent), no FK errors, and objects it created
  reattributed to `admin`.
- **Restore path:** reset an instance from a seed whose `admin` password is
  unknown/changed; confirm `local_admin`/`district` logs in afterward.
- **Backup path:** create a backup; confirm the resulting `.sql.gz` contains no
  active `local_admin` (grep the decompressed dump), and that `local_admin`
  still works on the live instance afterward.
- **Empty create:** create a `-v` no-seed instance; confirm `local_admin` works
  once booted (per the §4 approach chosen).
- No unit-testable pure logic here (all SQL/Docker/HTTP); verification is the
  live checks above plus `bash -n` on the new/changed scripts.
