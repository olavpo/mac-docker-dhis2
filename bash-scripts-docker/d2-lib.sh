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
  if command -v lsof >/dev/null 2>&1; then
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
    resolved=$(curl -s "https://s3-eu-west-1.amazonaws.com/releases.dhis2.org/?prefix=2.$major/" \
      | grep -o "dhis2-stable-2\.$major\.[0-9.]*.war" \
      | sort -V | tail -1 \
      | sed 's/dhis2-stable-//;s/.war//')
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

# Database readiness check
wait_for_db() {
  local db_container="$1"
  local max_attempts=30
  local attempt=0
  
  while [ $attempt -lt $max_attempts ]; do
    if docker exec "$db_container" psql -U dhis -d dhis2 -c "SELECT 1;" >/dev/null 2>&1; then
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
  
  return 1
}