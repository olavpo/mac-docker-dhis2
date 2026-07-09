# `local_admin` superuser — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every instance has a known superuser `local_admin`/`district`: added on restore and on empty creates, and stripped from backup dumps.

**Architecture:** A shared `bash-scripts-docker/d2-local-admin <instance> add|remove` script runs SQL in the instance's Postgres (via `docker exec … psql`). `d2-db-restore` calls `add` (Tomcat already down → cache fresh on restart); `d2-db-backup` wraps the dump in `remove → dump → add`; `d2-instance-create` runs `add` after an empty instance finishes booting. The broker needs no change.

**Tech stack:** Bash, PostgreSQL (psql / plpgsql), Docker. **No pure unit logic** — all behavior is validated live.

**Spec:** `docs/superpowers/specs/2026-07-09-local-admin-user-design.md`

---

## CRITICAL: this feature is developed against a live instance

The exact DHIS2 schema (columns on `userinfo`, sequence handling, 2FA/secret
column names) is **version-specific**. The SQL below is a correct-in-intent
starting point for DHIS2 2.41/2.42; the implementer MUST inspect the live schema
(`\d userinfo`, `\d userrole`) and adapt column lists where they differ, then
prove each behavior against a running instance. There is **no offline test** for
the SQL.

**Constants** (used throughout):
- username `local_admin`, password `district`
- bcrypt hash of `district`: `$2a$10$AY3Z6Z5b4gT8XKcqjq6zt.b3G0K2fH6mV3s7dS0rSxrqBcYb.7fWO`
- user uid `LocalAdmn01`, role uid `LocalAdmnR1`, role name `local_admin superuser`

---

## Task 0: stand up a test instance (prerequisite, not committed)

**Files:** none.

- [ ] **Step 1: Create a small seeded instance to develop against.**

There are currently no running instances. Create one with a demo seed (so it has
real users + org units — exercises the `admin`-exists and org-unit paths). Use
the CLI directly (repo working tree = current code):

```bash
export DHIS2_BASE=/Users/olavpo/dhis2
bash bash-scripts-docker/d2-instance-create -v 2.42 -s "$(ls "$DHIS2_BASE"/_seeds/*.sql.gz 2>/dev/null | head -1)" agent-la-dev
# if no seed exists, create empty: bash bash-scripts-docker/d2-instance-create -v 2.42 agent-la-dev
DBC=$(docker ps --format '{{.Names}}' | grep -E 'agent-la-dev[-_]db[-_]1')
echo "DB container: $DBC"
```

- [ ] **Step 2: Capture the live schema you'll code against.**

```bash
docker exec "$DBC" psql -U dhis -d dhis2 -c '\d userinfo'
docker exec "$DBC" psql -U dhis -d dhis2 -c '\d userrole'
docker exec "$DBC" psql -U dhis -d dhis2 -c '\d usermembership'
docker exec "$DBC" psql -U dhis -d dhis2 -c "\dt user*"
```
Note the actual NOT-NULL columns on `userinfo` and any 2FA/secret column names —
you will null those in the `add` clone (Task 1). Keep `agent-la-dev` for Tasks
1–5; delete it in Task 7.

---

## Task 1: `d2-local-admin` script + `add`

**Files:**
- Create: `bash-scripts-docker/d2-local-admin`

- [ ] **Step 1: Create the script skeleton (arg parsing + DB resolution).**

```bash
#!/bin/bash
#   Ensure/remove a known superuser `local_admin` (password: district) in an
#   instance's DHIS2 database. See docs/.../2026-07-09-local-admin-user-design.md
set -euo pipefail
IFS=$'\n\t'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/d2-lib.sh"

LA_USER="local_admin"
LA_USER_UID="LocalAdmn01"
LA_ROLE_UID="LocalAdmnR1"
LA_ROLE_NAME="local_admin superuser"
LA_PW_HASH='$2a$10$AY3Z6Z5b4gT8XKcqjq6zt.b3G0K2fH6mV3s7dS0rSxrqBcYb.7fWO'  # bcrypt('district')

[ -n "${DHIS2_BASE:-}" ] || { echo "Error: DHIS2_BASE not set" >&2; exit 1; }
usage() { echo "Usage: $0 <instance name> <add|remove>" >&2; exit 1; }
[ $# -eq 2 ] || usage
INSTANCE=$1; ACTION=$2
[ -d "$DHIS2_BASE/$INSTANCE" ] || { echo "Error: instance $INSTANCE does not exist" >&2; exit 1; }
DBC=$(resolve_db_container "$INSTANCE") || { echo "Error: DB container for $INSTANCE not found" >&2; exit 1; }
if [ "$(docker inspect -f '{{.State.Status}}' "$DBC" 2>/dev/null)" != "running" ]; then
  echo "Error: DB container $DBC is not running" >&2; exit 1
fi

psql_exec() { docker exec -i "$DBC" psql -U dhis -d dhis2 -v ON_ERROR_STOP=1 "$@"; }

la_add() {
  psql_exec <<SQL
BEGIN;
-- clean slate (assumes local_admin has no FK refs yet: true right after a
-- restore/create; a backup never contains local_admin)
DELETE FROM usermembership WHERE userinfoid IN (SELECT userinfoid FROM userinfo WHERE username='${LA_USER}');
DELETE FROM userrolemembers WHERE userid IN (SELECT userinfoid FROM userinfo WHERE username='${LA_USER}');
DELETE FROM userinfo WHERE username='${LA_USER}';
DELETE FROM userroleauthorities WHERE userroleid IN (SELECT userroleid FROM userrole WHERE uid='${LA_ROLE_UID}');
DELETE FROM userrolemembers    WHERE userroleid IN (SELECT userroleid FROM userrole WHERE uid='${LA_ROLE_UID}');
DELETE FROM userrole WHERE uid='${LA_ROLE_UID}';

-- dedicated Superuser role (ALL authority)
INSERT INTO userrole (userroleid, uid, name, created, lastupdated)
VALUES ((SELECT COALESCE(MAX(userroleid),0)+1 FROM userrole), '${LA_ROLE_UID}', '${LA_ROLE_NAME}', now(), now());
INSERT INTO userroleauthorities (userroleid, authority)
VALUES ((SELECT userroleid FROM userrole WHERE uid='${LA_ROLE_UID}'), 'ALL');

-- local_admin user: clone an existing userinfo row for column-completeness,
-- then override identity. ADAPT the null-outs below to the live schema
-- (Task 0 step 2): null any 2FA/secret/openid/ldap columns that exist.
CREATE TEMP TABLE _la ON COMMIT DROP AS SELECT * FROM userinfo ORDER BY userinfoid LIMIT 1;
UPDATE _la SET
  userinfoid=(SELECT COALESCE(MAX(userinfoid),0)+1 FROM userinfo),
  uid='${LA_USER_UID}', code=NULL, username='${LA_USER}', password='${LA_PW_HASH}',
  firstname='Local', surname='Admin', disabled=false,
  email=NULL, phonenumber=NULL, openid=NULL, ldapid=NULL,
  created=now(), lastupdated=now(), lastupdatedby=NULL;
INSERT INTO userinfo SELECT * FROM _la;

-- role membership
INSERT INTO userrolemembers (userroleid, userid)
VALUES ((SELECT userroleid FROM userrole WHERE uid='${LA_ROLE_UID}'),
        (SELECT userinfoid FROM userinfo WHERE username='${LA_USER}'));

-- data-capture org units = root org unit(s) (none inserted if DB has no OUs)
INSERT INTO usermembership (userinfoid, organisationunitid)
SELECT (SELECT userinfoid FROM userinfo WHERE username='${LA_USER}'), organisationunitid
FROM organisationunit WHERE parentid IS NULL;
COMMIT;
SQL
}

case "$ACTION" in
  add)    la_add;    echo "✅ local_admin ensured on $INSTANCE (password: district)";;
  remove) la_remove; echo "✅ local_admin removed from $INSTANCE";;
  *) usage;;
esac
```

(You will add `la_remove` in Task 2; for this task, temporarily stub it as
`la_remove() { :; }` so the script parses, or implement Task 2 before running
`remove`.)

- [ ] **Step 2: `chmod +x` and syntax-check.**

Run: `chmod +x bash-scripts-docker/d2-local-admin && bash -n bash-scripts-docker/d2-local-admin && echo SYNTAX_OK`

- [ ] **Step 3: Run `add` against the live instance and ADAPT to the schema.**

```bash
export DHIS2_BASE=/Users/olavpo/dhis2
bash bash-scripts-docker/d2-local-admin agent-la-dev add
```
If psql raises an error about a column (e.g. a NOT-NULL 2FA/secret column not
nulled, or a column that doesn't exist in the `UPDATE _la SET …`), adjust the
clone's SET list per the Task 0 schema dump and re-run. Iterate until it
completes without error.

- [ ] **Step 4: Verify the superuser works (this is the acceptance test).**

The dev instance must be booted. Then:
```bash
PORT=$(grep -oE '[0-9]+:8080' "$DHIS2_BASE/agent-la-dev/docker-compose.yml" | cut -d: -f1)
# restart tomcat so the (freshly restored) instance picks up local_admin, or wait for boot
curl -s -u local_admin:district "http://localhost:$PORT/api/me?fields=userCredentials,username,authorities" | python3 -m json.tool | head -30
curl -s -o /dev/null -w '%{http_code}\n' -u local_admin:district "http://localhost:$PORT/api/me"
```
Expected: `200`; `authorities` includes `ALL`. Re-run `add` → still exactly one
`local_admin` (idempotent):
```bash
docker exec "$DBC" psql -U dhis -d dhis2 -tc "SELECT count(*) FROM userinfo WHERE username='local_admin';"   # expect 1
```

- [ ] **Step 5: Commit** (once `add` is verified against the live schema)

```bash
git add bash-scripts-docker/d2-local-admin
git commit -m "feat: d2-local-admin add — ensure known local_admin superuser via SQL"
```

(Append to every commit body in this plan:
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`)

---

## Task 2: `d2-local-admin remove` (reassign→delete; scrub fallback)

**Files:**
- Modify: `bash-scripts-docker/d2-local-admin` (implement `la_remove`)

- [ ] **Step 1: Implement `la_remove`.** Replace the stub with:

```bash
la_remove() {
  psql_exec <<SQL
BEGIN;
DO \$\$
DECLARE la_id bigint; admin_id bigint; r record;
BEGIN
  SELECT userinfoid INTO la_id FROM userinfo WHERE username='${LA_USER}';
  IF la_id IS NULL THEN RETURN; END IF;
  SELECT userinfoid INTO admin_id FROM userinfo WHERE username='admin';
  IF admin_id IS NOT NULL THEN
    -- repoint every FK referencing userinfo(userinfoid) from local_admin to admin,
    -- EXCEPT the junction tables (local_admin's own rows, deleted below; reassigning
    -- them would collide on their unique keys since local_admin shares admin's root OU)
    FOR r IN
      SELECT tc.table_name AS t, kcu.column_name AS c
      FROM information_schema.table_constraints tc
      JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name
      JOIN information_schema.constraint_column_usage ccu ON tc.constraint_name=ccu.constraint_name
      WHERE tc.constraint_type='FOREIGN KEY'
        AND ccu.table_name='userinfo' AND ccu.column_name='userinfoid'
        AND tc.table_name NOT IN ('usermembership','userrolemembers')
    LOOP
      EXECUTE format('UPDATE %I SET %I=%L WHERE %I=%L', r.t, r.c, admin_id, r.c, la_id);
    END LOOP;
    DELETE FROM usermembership WHERE userinfoid=la_id;
    DELETE FROM userrolemembers WHERE userid=la_id;
    DELETE FROM userinfo WHERE userinfoid=la_id;
    DELETE FROM userroleauthorities WHERE userroleid IN (SELECT userroleid FROM userrole WHERE uid='${LA_ROLE_UID}');
    DELETE FROM userrolemembers    WHERE userroleid IN (SELECT userroleid FROM userrole WHERE uid='${LA_ROLE_UID}');
    DELETE FROM userrole WHERE uid='${LA_ROLE_UID}';
  ELSE
    -- no admin to reassign to: scrub (disable + unusable password, keep the row)
    UPDATE userinfo SET password='!disabled-'||md5(random()::text), disabled=true WHERE userinfoid=la_id;
  END IF;
END
\$\$;
COMMIT;
SQL
}
```

Note the `\$\$` escaping: inside a bash double-quoted heredoc, `$$` must be
written `\$\$` so psql receives literal `$$` for the DO block. `${LA_...}` bash
vars still expand. (If you prefer, use an unquoted heredoc delimiter and escape
the `${...}` differently — but keep the DO block's `$$` literal.)

- [ ] **Step 2: Verify remove after the agent has "used" local_admin (FK refs).**

Create an object as local_admin so it leaves FK stamps, then remove:
```bash
export DHIS2_BASE=/Users/olavpo/dhis2
PORT=$(grep -oE '[0-9]+:8080' "$DHIS2_BASE/agent-la-dev/docker-compose.yml" | cut -d: -f1)
# create a trivial object as local_admin (stamps createdby/lastupdatedby):
curl -s -u local_admin:district -X POST -H 'Content-Type: application/json' \
  -d '{"name":"la-test-cog","dataDimensionType":"DISALLOWED"}' \
  "http://localhost:$PORT/api/categoryOptionGroups" >/dev/null || true
bash bash-scripts-docker/d2-local-admin agent-la-dev remove
docker exec "$DBC" psql -U dhis -d dhis2 -tc "SELECT count(*) FROM userinfo WHERE username='local_admin';"   # expect 0
```
Expected: `remove` exits 0 with **no FK errors**, count `0`. (If it errors on an
FK, a table with a userinfo FK wasn't caught by the loop — inspect and widen the
loop, but do not reassign the two junction tables.)

- [ ] **Step 3: Verify the scrub fallback.** On a throwaway where `admin` is
gone:
```bash
docker exec "$DBC" psql -U dhis -d dhis2 -c "UPDATE userinfo SET username='admin_orig' WHERE username='admin';"
bash bash-scripts-docker/d2-local-admin agent-la-dev add
bash bash-scripts-docker/d2-local-admin agent-la-dev remove
docker exec "$DBC" psql -U dhis -d dhis2 -tc "SELECT disabled FROM userinfo WHERE username='local_admin';"   # expect t (scrubbed, still present)
docker exec "$DBC" psql -U dhis -d dhis2 -c "UPDATE userinfo SET username='admin' WHERE username='admin_orig';"  # restore
bash bash-scripts-docker/d2-local-admin agent-la-dev add   # leave it added for later tasks
```

- [ ] **Step 4: Commit**

```bash
git add bash-scripts-docker/d2-local-admin
git commit -m "feat: d2-local-admin remove — reassign-then-delete, scrub fallback"
```

---

## Task 3: wire into `d2-db-restore`

**Files:**
- Modify: `bash-scripts-docker/d2-db-restore`

- [ ] **Step 1: Call `add` after restore, before the Tomcat restart.** Read the
live file; find the success point after cleanup (`rm -f "$TEMP_SQL"`) and
**before** the "Restart Tomcat container" block. Insert:

```bash
# Ensure the known local_admin superuser exists regardless of what was restored.
# Tomcat is still stopped here, so the restart below refreshes the user cache.
echo "Ensuring local_admin superuser..."
if ! "$SCRIPT_DIR/d2-local-admin" "$INSTANCE" add; then
  echo "⚠️  Warning: could not ensure local_admin (restore itself succeeded)"
fi
```

- [ ] **Step 2: Syntax check.** `bash -n bash-scripts-docker/d2-db-restore && echo SYNTAX_OK`

- [ ] **Step 3: Live verify.** Restore the dev instance from a seed and confirm
`local_admin`/`district` works afterward:
```bash
export DHIS2_BASE=/Users/olavpo/dhis2
SEED=$(ls "$DHIS2_BASE"/_seeds/*.sql.gz | head -1)
bash bash-scripts-docker/d2-db-restore agent-la-dev "$SEED"   # stops tomcat, restores, adds local_admin, restarts
# wait for boot, then:
PORT=$(grep -oE '[0-9]+:8080' "$DHIS2_BASE/agent-la-dev/docker-compose.yml" | cut -d: -f1)
curl -s -o /dev/null -w '%{http_code}\n' -u local_admin:district "http://localhost:$PORT/api/me"   # expect 200
```

- [ ] **Step 4: Commit**

```bash
git add bash-scripts-docker/d2-db-restore
git commit -m "feat(restore): ensure local_admin superuser after restore"
```

---

## Task 4: wire into `d2-db-backup`

**Files:**
- Modify: `bash-scripts-docker/d2-db-backup`

- [ ] **Step 1: Wrap the dump with remove → dump → re-add.** Read the live file;
find the `docker exec "$DB_CONTAINER" pg_dump … | gzip > "$BACKUP_FILE"` line.
Immediately before it, add the removal + a trap that always re-adds:

```bash
# Strip the known-password local_admin from the dump; always re-add afterward.
"$SCRIPT_DIR/d2-local-admin" "$INSTANCE" remove || echo "⚠️  local_admin remove failed; dump may contain it"
trap '"$SCRIPT_DIR/d2-local-admin" "$INSTANCE" add >/dev/null 2>&1 || true' EXIT
```
(`SCRIPT_DIR` is already defined at the top of `d2-db-backup`. Confirm; if not,
add `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"`.) The `pg_dump`
line and the rest stay unchanged; the `EXIT` trap re-adds `local_admin` whether
the dump succeeds or fails.

- [ ] **Step 2: Syntax check.** `bash -n bash-scripts-docker/d2-db-backup && echo SYNTAX_OK`

- [ ] **Step 3: Live verify the dump is clean and local_admin survives.**
```bash
export DHIS2_BASE=/Users/olavpo/dhis2
bash bash-scripts-docker/d2-db-backup agent-la-dev
BK=$(ls -t "$DHIS2_BASE"/_backups/agent-la-dev/*.sql.gz | head -1)
gunzip -c "$BK" | grep -c "local_admin" || echo "0 (good: not in dump)"   # expect 0
docker exec "$DBC" psql -U dhis -d dhis2 -tc "SELECT count(*) FROM userinfo WHERE username='local_admin';"  # expect 1 (re-added)
```
Expected: `0` occurrences in the dump; `1` on the live DB afterward.

- [ ] **Step 4: Commit**

```bash
git add bash-scripts-docker/d2-db-backup
git commit -m "feat(backup): strip local_admin from dumps (remove -> dump -> re-add)"
```

---

## Task 5: wire into `d2-instance-create` (empty creates)

**Files:**
- Modify: `bash-scripts-docker/d2-instance-create`

- [ ] **Step 1: After an empty (version, no seed) create finishes, ensure
local_admin via SQL once DHIS2 has booted.** Read the live file; find the tail
where deployment is done (after the WAR/DB block, near the final success echo).
Add:

```bash
# Empty instance (version, no seed): once DHIS2 has initialized its schema,
# ensure the known local_admin superuser. Best-effort — never fails the create.
if [ -n "$VERSION" ] && [ -z "$DB_SEED" ]; then
  echo "Waiting for DHIS2 to initialize before adding local_admin..."
  for i in $(seq 1 60); do
    if curl -s -u admin:district -o /dev/null -w '%{http_code}' \
         "http://localhost:$HTTP_PORT/api/system/info" | grep -q '^200$'; then
      "$SCRIPT_DIR/d2-local-admin" "$INSTANCE" add \
        && echo "✅ local_admin ensured" \
        || echo "⚠️  could not add local_admin (admin/district still works)"
      break
    fi
    sleep 10
  done
fi
```
(No Tomcat restart needed: `local_admin` has never been looked up, so nothing is
cached for it — its first login loads fresh. `SCRIPT_DIR` and `HTTP_PORT` are
already defined earlier in the script; confirm.)

- [ ] **Step 2: Syntax check.** `bash -n bash-scripts-docker/d2-instance-create && echo SYNTAX_OK`

- [ ] **Step 3: Live verify on a fresh empty instance.**
```bash
export DHIS2_BASE=/Users/olavpo/dhis2
bash bash-scripts-docker/d2-instance-create -v 2.42 agent-la-empty   # waits for boot, adds local_admin
PORT=$(grep -oE '[0-9]+:8080' "$DHIS2_BASE/agent-la-empty/docker-compose.yml" | cut -d: -f1)
curl -s -o /dev/null -w '%{http_code}\n' -u local_admin:district "http://localhost:$PORT/api/me"   # expect 200
bash bash-scripts-docker/d2-instance-delete agent-la-empty
```

- [ ] **Step 4: Commit**

```bash
git add bash-scripts-docker/d2-instance-create
git commit -m "feat(create): ensure local_admin on empty instances after boot"
```

---

## Task 6: documentation

**Files:**
- Modify: `docs/broker-api.md`, `docs/broker.md`, `skills/dhis2-instances/SKILL.md`, `README.md`

- [ ] **Step 1: `skills/dhis2-instances/SKILL.md`** — this is the highest-value
doc (agents rely on it). In "What you can and cannot do" (or near credentials),
add:

```markdown
- **Guaranteed superuser:** every instance you create or reset has a
  `local_admin` / `district` superuser (`ALL` authority), regardless of the
  restored database's own `admin`. Prefer it over `admin` when a seed's `admin`
  is disabled or has an unknown password. (It is stripped from backups, so it
  never appears in dumps.)
```

- [ ] **Step 2: `docs/broker-api.md` and `docs/broker.md`** — add a short note
under the reset/backup/create endpoints that a `local_admin`/`district`
superuser is ensured on restore/create and stripped from backups.

- [ ] **Step 3: `README.md`** — add `d2-local-admin` to the script list under
"Database" or "Application & info":

```markdown
  - `d2-local-admin` — ensure/remove the known `local_admin` superuser in an instance DB
```

- [ ] **Step 4: Commit**

```bash
git add docs/broker-api.md docs/broker.md skills/dhis2-instances/SKILL.md README.md
git commit -m "docs: document the guaranteed local_admin superuser"
```

---

## Task 7: final end-to-end verification + cleanup

**Files:** none.

- [ ] **Step 1: Full sweep on the dev instance** (already has local_admin):
  - `GET /api/me` as `local_admin:district` → `200`, `ALL` authority present.
  - `d2-db-backup agent-la-dev` → dump has no `local_admin`; live still has it.
  - `d2-db-restore agent-la-dev <seed>` → `local_admin` works after restart.
- [ ] **Step 2: Delete the dev instances.**
```bash
export DHIS2_BASE=/Users/olavpo/dhis2
bash bash-scripts-docker/d2-instance-delete agent-la-dev
# (agent-la-empty already deleted in Task 5)
```
- [ ] **Step 3: Commit** only if Step 1 surfaced fixes.

---

## Self-review notes

- **Spec coverage:** `d2-local-admin add` (Task 1) with dedicated ALL role +
  root data-capture OUs + clone-row completeness; `remove` reassign→delete +
  scrub (Task 2); restore wiring with Tomcat-down cache refresh (Task 3); backup
  remove→dump→re-add with EXIT trap (Task 4); empty-create SQL-after-boot,
  best-effort, no restart (Task 5); docs (Task 6). Broker unchanged (spec §5).
- **Naming:** `local_admin`/`district`, uids `LocalAdmn01`/`LocalAdmnR1`, role
  name, bcrypt constant used identically across tasks.
- **Live-validation honesty:** Task 0 captures the schema; Tasks 1–5 each verify
  against the running dev instance because the SQL is version-specific and has
  no offline test. The `remove` reassign loop excludes `usermembership` /
  `userrolemembers` (per spec) and deletes `userroleauthorities` before
  `userrole`.
- **Risk to watch during implementation:** the `add` clone's null-out list
  (2FA/secret/openid/ldap columns) is the most version-sensitive part — adapt it
  to the Task 0 schema dump. If `add`'s initial delete ever hits an FK (local_admin
  already has refs), run `remove` first.
