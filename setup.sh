#!/bin/bash
# Setup script for DHIS2 Docker tools
#
# Usage:
#   ./setup.sh install         - First-time setup: copy templates, create symlinks
#   ./setup.sh update          - Update symlinks, add new templates, report drift
#                                (never overwrites a customised template)
#   ./setup.sh sync-templates  - Overwrite installed templates with repo versions

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="/usr/local/bin"

usage() {
  echo "Usage: $0 <install|update|sync-templates>"
  echo ""
  echo "  install         First-time setup: copies templates to \$DHIS2_BASE/_templates/"
  echo "                  and creates symlinks in $TARGET_DIR"
  echo "  update          Updates symlinks, adds new templates, reports drifted ones"
  echo "                  — does not overwrite customised templates"
  echo "  sync-templates  Overwrites the installed templates with the repo versions"
  exit 1
}

if [ $# -ne 1 ]; then
  usage
fi

MODE="$1"

case "$MODE" in
  install|update|sync-templates) ;;
  *) usage ;;
esac

# Check for required environment variable
if [ -z "${DHIS2_BASE:-}" ]; then
  echo "Error: DHIS2_BASE environment variable not set"
  exit 1
fi

create_symlinks() {
  local added=0
  for script in "$SCRIPT_DIR"/bash-scripts-docker/d2-*; do
    if [ -f "$script" ]; then
      script_name=$(basename "$script")
      echo "  $TARGET_DIR/$script_name -> $script"
      sudo ln -sf "$script" "$TARGET_DIR/$script_name"
      added=$((added + 1))
    fi
  done
  echo "  $added symlink(s) created/updated"
}

copy_templates() {
  mkdir -p "$DHIS2_BASE/_templates"
  # -R: _templates now contains subdirectories (doris/)
  cp -Rf "$SCRIPT_DIR/_templates/"* "$DHIS2_BASE/_templates/"
  echo "  Templates copied to $DHIS2_BASE/_templates/"
}

# Add templates that don't exist yet, but never overwrite existing ones —
# they may carry user customisations (dhis.conf settings, port tweaks, etc.).
copy_new_templates() {
  mkdir -p "$DHIS2_BASE/_templates"
  local added=0
  for tpl in "$SCRIPT_DIR/_templates/"*; do
    local name
    name=$(basename "$tpl")
    if [ ! -e "$DHIS2_BASE/_templates/$name" ]; then
      # -R: a new subdirectory (e.g. doris/) is copied whole; existing
      # entries are left untouched by the guard above.
      cp -R "$tpl" "$DHIS2_BASE/_templates/$name"
      echo "  Added new template: $name"
      added=$((added + 1))
    fi
  done
  if [ "$added" -eq 0 ]; then
    echo "  No new templates (existing ones left untouched)"
  fi
}

# Report templates whose repo version differs from the installed copy.
# copy_new_templates never overwrites, so a template edited in the repo stays
# invisible to `update` — the Postgres tuning added in 9a78b26 sat unused for a
# day that way. Report the drift; syncing stays an explicit choice.
# Iterates over repo files only, so live-only files (the cached pgJDBC jar)
# are not flagged.
report_template_drift() {
  local drifted=0
  local rel
  while IFS= read -r rel; do
    [ -f "$DHIS2_BASE/_templates/$rel" ] || continue
    if ! cmp -s "$SCRIPT_DIR/_templates/$rel" "$DHIS2_BASE/_templates/$rel"; then
      echo "  DRIFT: $rel"
      drifted=$((drifted + 1))
    fi
  done < <(cd "$SCRIPT_DIR/_templates" && find . -type f ! -name '.DS_Store' \
             | sed 's|^\./||' | LC_ALL=C sort)

  if [ "$drifted" -eq 0 ]; then
    echo "  No drift (installed templates match the repo)"
    return
  fi
  echo ""
  echo "  $drifted template(s) differ from the repo. Only newly created"
  echo "  instances read these — existing instances keep their own copies."
  echo "  Run '$0 sync-templates' to overwrite the installed versions."
}

if [ "$MODE" = "install" ]; then
  echo "Installing DHIS2 Docker tools..."
  echo ""
  echo "Copying templates..."
  copy_templates
  echo ""
  echo "Creating symlinks in $TARGET_DIR ..."
  create_symlinks
  echo ""
  echo "Done. You can now run d2-info, d2-instance-create, etc. from anywhere."

elif [ "$MODE" = "update" ]; then
  echo "Updating DHIS2 Docker tools..."
  echo ""
  echo "Checking for new templates..."
  copy_new_templates
  echo ""
  echo "Checking for drifted templates..."
  report_template_drift
  echo ""
  echo "Updating symlinks in $TARGET_DIR ..."
  create_symlinks
  echo ""
  echo "Done."

elif [ "$MODE" = "sync-templates" ]; then
  echo "Syncing templates (overwriting installed versions)..."
  echo ""
  copy_templates
  echo ""
  echo "Done. Newly created instances pick these up; existing instances keep"
  echo "their own copies."
fi
