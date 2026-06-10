#!/bin/bash
# Setup script for DHIS2 Docker tools
#
# Usage:
#   ./setup.sh install   - First-time setup: copy templates and create symlinks
#   ./setup.sh update    - Update symlinks only (preserves customised templates)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="/usr/local/bin"

usage() {
  echo "Usage: $0 <install|update>"
  echo ""
  echo "  install  First-time setup: copies templates to \$DHIS2_BASE/_templates/"
  echo "           and creates symlinks in $TARGET_DIR"
  echo "  update   Updates symlinks only — does not overwrite customised templates"
  exit 1
}

if [ $# -ne 1 ]; then
  usage
fi

MODE="$1"

if [ "$MODE" != "install" ] && [ "$MODE" != "update" ]; then
  usage
fi

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
  cp -f "$SCRIPT_DIR/_templates/"* "$DHIS2_BASE/_templates/"
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
      cp "$tpl" "$DHIS2_BASE/_templates/$name"
      echo "  Added new template: $name"
      added=$((added + 1))
    fi
  done
  if [ "$added" -eq 0 ]; then
    echo "  No new templates (existing ones left untouched)"
  fi
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
  echo "Updating symlinks in $TARGET_DIR ..."
  create_symlinks
  echo ""
  echo "Done."
fi
