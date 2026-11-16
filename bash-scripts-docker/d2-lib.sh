#!/bin/bash
# d2-lib.sh - Common functions for DHIS2 Docker scripts

# Container resolution functions
resolve_db_container() {
  local instance="$1"
  if docker ps -a --format '{{.Names}}' | grep -q "^${instance}-db-1$"; then
    echo "${instance}-db-1"
  elif docker ps -a --format '{{.Names}}' | grep -q "^${instance}_db_1$"; then
    echo "${instance}_db_1"
  else
    return 1
  fi
}

resolve_tomcat_container() {
  local instance="$1"
  if docker ps -a --format '{{.Names}}' | grep -q "^${instance}-tomcat-1$"; then
    echo "${instance}-tomcat-1"
  elif docker ps -a --format '{{.Names}}' | grep -q "^${instance}_tomcat_1$"; then
    echo "${instance}_tomcat_1"
  else
    return 1
  fi
}

# Path resolution
abspath() {
  local p="$1"
  [[ "$p" == /* ]] && { echo "$p"; return; }
  if [[ "$p" == ./* ]]; then
    echo "$(pwd)/${p:2}"
  else
    echo "$(pwd)/$p"
  fi
}

# Port checking
is_port_in_use() {
  local port="$1"
  # Prefer a connect probe: lsof walks every process's open files and can
  # block in the kernel indefinitely (seen with a sluggish SMB mount on
  # macOS), hanging d2-instance-create before it does anything. Docker and
  # most dev servers listen on 0.0.0.0/127.0.0.1, which this catches.
  if command -v nc >/dev/null 2>&1; then
    if nc -z -w 2 127.0.0.1 "$port" >/dev/null 2>&1; then
      return 0
    fi
    return 1
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1
  elif command -v ss >/dev/null 2>&1; then
    ss -tuln | grep -q ":$port "
  elif command -v netstat >/dev/null 2>&1; then
    netstat -an 2>/dev/null | grep -q "[.:]$port "
  else
    return 1
  fi
}

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

# Release WARs downloaded by version are kept here so repeat deploys skip the
# download. Each use refreshes the file's mtime; files unused for
# D2_WAR_CACHE_DAYS (default 7) are pruned on the next deploy.
war_cache_dir() {
  echo "${DHIS2_BASE}/_war-cache"
}

prune_war_cache() {
  local days="${D2_WAR_CACHE_DAYS:-7}"
  [ -d "$(war_cache_dir)" ] || return 0
  find "$(war_cache_dir)" -maxdepth 1 -name '*.war' -mtime +"$days" -delete 2>/dev/null || true
  find "$(war_cache_dir)" -maxdepth 1 -name '*.tmp' -mtime +1 -delete 2>/dev/null || true
}

# Version normalization
normalize_version() {
  local v="$1"
  # "2.42" means major 42 — resolve like "42" below. Without this, the X.Y
  # rule would turn it into the nonsense version "2.2.42".
  if [[ "$v" =~ ^2\.([0-9]+)$ ]]; then
    v="${BASH_REMATCH[1]}"
  fi
  if [[ "$v" =~ ^[0-9]+\.[0-9]+$ ]]; then
    echo "2.${v}"
  elif [[ "$v" =~ ^[0-9]+$ ]]; then
    local major="$v"
    local resolved
    resolved=$(curl -s --max-time 20 "https://s3-eu-west-1.amazonaws.com/releases.dhis2.org/?prefix=2.$major/" \
      | grep -o "dhis2-stable-2\.$major\.[0-9.]*.war" \
      | sort -V | tail -1 \
      | sed 's/dhis2-stable-//;s/.war//') || true
    if [ -z "$resolved" ]; then
      # Offline or S3 unreachable: fall back to the newest cached WAR.
      resolved=$(ls "$(war_cache_dir)" 2>/dev/null \
        | grep -o "^dhis2-stable-2\.$major\.[0-9.]*.war$" \
        | sort -V | tail -1 \
        | sed 's/dhis2-stable-//;s/.war//') || true
      if [ -n "$resolved" ]; then
        echo "Warning: could not reach releases.dhis2.org; using cached $resolved" >&2
      fi
    fi
    if [ -z "$resolved" ]; then
      echo "Error: could not resolve version for major $major" >&2
      return 1
    fi
    echo "$resolved"
  else
    echo "$v"
  fi
}

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

# Get DHIS2 major version from flyway_schema_history (e.g. "41" from "2.41.7")
get_db_major_version() {
  local db_container="$1"
  local full_version
  full_version=$(docker exec "$db_container" psql -U dhis -d dhis2 -t -c \
    "SELECT version FROM flyway_schema_history ORDER BY installed_rank DESC LIMIT 1;" \
    2>/dev/null | tr -d '[:space:]')
  [ -n "$full_version" ] || return 1
  echo "$full_version" | cut -d '.' -f 2
}


# Doris (analytics backend) container for an instance, if any.
resolve_doris_container() {
  local instance="$1"
  if docker ps -a --format '{{.Names}}' | grep -q "^${instance}-doris-1$"; then
    echo "${instance}-doris-1"
  elif docker ps -a --format '{{.Names}}' | grep -q "^${instance}_doris_1$"; then
    echo "${instance}_doris_1"
  else
    return 1
  fi
}

# One-time Doris initialization after the container is healthy. DHIS2
# creates its pg_dhis catalog itself but NOT the analytics database; and
# without spill, the aggregate datavalue load blows the laptop-sized BE
# memory cap (see docs/doris/spike-findings-2026-07-14.md). Globals persist
# in FE meta (a volume), but setting them is idempotent so we do it on
# every create.
doris_init() {
  local instance="$1"
  local container
  container=$(resolve_doris_container "$instance") || {
    echo "Error: Doris container for instance $instance not found" >&2
    return 1
  }
  docker exec "$container" mysql -h127.0.0.1 -P9030 -uroot \
    -e "CREATE DATABASE IF NOT EXISTS analytics;" || return 1
  # 3.0.x spill variable names (enable_spill does not exist there).
  docker exec "$container" mysql -h127.0.0.1 -P9030 -uroot \
    -e "SET GLOBAL enable_force_spill = true;
        SET GLOBAL enable_sort_spill = true;
        SET GLOBAL enable_agg_spill = true;
        SET GLOBAL parallel_pipeline_task_num = 1;" || return 1
}

# Database readiness check. Connects over TCP, not the unix socket: on a fresh
# volume the postgres image's entrypoint first runs a temporary server that
# listens on the socket only (initdb + the postgis init scripts), then shuts
# it down and starts the real one. A socket check passes against that
# temporary server, and a restore started then dies with "terminating
# connection due to administrator command" (psql exit 2).
wait_for_db() {
  local db_container="$1"
  local max_attempts=30
  local attempt=0

  while [ $attempt -lt $max_attempts ]; do
    if docker exec -e PGPASSWORD=dhis "$db_container" \
         psql -h 127.0.0.1 -U dhis -d dhis2 -c "SELECT 1;" >/dev/null 2>&1; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
  
  return 1
}

# Verify that the Tomcat container sees the instance directory as it is now.
# Docker Desktop's VirtioFS file sharing caches directory entries by host
# path: after a delete, a container that bind-mounts the same path can see
# the OLD directory (no ROOT.war, ghost log files) for its whole life, and
# Tomcat starts with nothing deployed. d2-instance-create avoids reusing
# mount paths (see .mounts-* there); this check catches anything that slips
# through, so a create fails at once instead of a boot-wait that never ends.
check_fresh_mounts() {
  local instance="$1" home="$2"
  local token d tomcat seen
  token="$(date +%s)-$$-$RANDOM"
  for d in DHIS2_HOME webapps logs temp; do
    echo "$token" > "$home/$d/.mount-check"
  done
  tomcat=$(resolve_tomcat_container "$instance") || return 1
  seen=$(docker exec "$tomcat" sh -c 'cat /DHIS2_home/.mount-check \
    /usr/local/tomcat/webapps/.mount-check /usr/local/tomcat/logs/.mount-check \
    /usr/local/tomcat/temp/.mount-check' 2>/dev/null | sort -u || true)
  for d in DHIS2_HOME webapps logs temp; do
    rm -f "$home/$d/.mount-check"
  done
  if [ "$seen" != "$token" ]; then
    echo "Error: Tomcat sees a stale copy of $home (Docker Desktop file-sharing" \
         "cache). Restart Docker Desktop, or recreate under another name." >&2
    return 1
  fi
}
