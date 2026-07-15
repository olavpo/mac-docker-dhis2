#!/bin/bash
# Tests for the pure (offline) helpers in d2-lib.sh — the bash mirror of the
# version logic that d2-broker duplicates and covers in test_d2_broker.py.
# Network-dependent paths (major-only resolution via releases.dhis2.org) are
# deliberately not exercised.
# Run: bash test_d2_lib.sh   (from bash-scripts-docker/)

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/d2-lib.sh"

FAILURES=0
TESTS=0

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  TESTS=$((TESTS + 1))
  if [ "$expected" = "$actual" ]; then
    echo "ok   $desc"
  else
    echo "FAIL $desc: expected '$expected', got '$actual'"
    FAILURES=$((FAILURES + 1))
  fi
}

# --- normalize_version (offline paths only) ---------------------------------
assert_eq "normalize_version X.Y -> 2.X.Y" "2.41.2" "$(normalize_version "41.2")"
assert_eq "normalize_version full version passes through" "2.42.4" "$(normalize_version "2.42.4")"
assert_eq "normalize_version snapshot-ish string passes through" "2.42.4-rc" "$(normalize_version "2.42.4-rc")"

# --- dhis2_major -------------------------------------------------------------
assert_eq "dhis2_major 2.42.4" "42" "$(dhis2_major "2.42.4")"
assert_eq "dhis2_major 2.42" "42" "$(dhis2_major "2.42")"
assert_eq "dhis2_major bare major" "42" "$(dhis2_major "42")"
assert_eq "dhis2_major 41.4" "41" "$(dhis2_major "41.4")"

# --- required_tomcat_for_major ----------------------------------------------
assert_eq "tomcat for 40" "9" "$(required_tomcat_for_major "40")"
assert_eq "tomcat for 41" "9" "$(required_tomcat_for_major "41")"
assert_eq "tomcat for 42" "10" "$(required_tomcat_for_major "42")"
assert_eq "tomcat for 43" "10" "$(required_tomcat_for_major "43")"
assert_eq "tomcat for junk defaults to 10" "10" "$(required_tomcat_for_major "junk")"

# --- instance-name regex (mirrors NAME_RE in d2-broker) ----------------------
name_ok() {
  [[ "$1" =~ ^[a-z][a-z0-9_-]{1,29}$ ]] && echo yes || echo no
}
assert_eq "name: plain" "yes" "$(name_ok "myinstance")"
assert_eq "name: agent prefix" "yes" "$(name_ok "agent-test1")"
assert_eq "name: 30 chars ok" "yes" "$(name_ok "a23456789012345678901234567890")"
assert_eq "name: 31 chars rejected" "no" "$(name_ok "a234567890123456789012345678901")"
assert_eq "name: space rejected" "no" "$(name_ok "bad name")"
assert_eq "name: slash rejected" "no" "$(name_ok "bad/name")"
assert_eq "name: leading underscore rejected" "no" "$(name_ok "_seeds")"
assert_eq "name: uppercase rejected" "no" "$(name_ok "Upper")"
assert_eq "name: single char rejected" "no" "$(name_ok "a")"

echo ""
echo "$((TESTS - FAILURES))/$TESTS passed"
[ "$FAILURES" -eq 0 ]
