#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  tools/prepopulate_esphome_deps.sh [config-path]

Purpose:
  Sync the source repo into a no-space working directory, then run
  `esphome config` and `esphome compile` to pre-populate or refresh the
  ESPHome / PlatformIO / ESP-IDF dependency caches.

Defaults:
  config-path: esphome/validate_display_preset.yaml

Environment overrides:
  PROFLAME2_SOURCE_REPO   Source repo root. Defaults to script repo root.
  PROFLAME2_BUILD_ROOT    Parent for a unique temporary build workspace.
                          Default: ${TMPDIR:-/tmp}
  PROFLAME2_BUILD_DIR     Optional new, non-existing workspace path.
                          Removed on exit; existing paths are never overwritten.
  ESPHOME_CLI             Optional ESPHome executable path.
  PLATFORMIO_CORE_DIR     PlatformIO cache location.
  PIO_HOME_DIR            PlatformIO cache location.

Notes:
  - Safe to run repeatedly. Each run uses a fresh temporary worktree and lets
    ESPHome / PlatformIO update cached dependencies as needed.
  - This script does not treat the build worktree as authoritative source.
  - If dependencies are already cached, later runs are typically much faster.
  - The temporary worktree is removed on success, failure, or interruption.
    Persistent dependency caches outside that worktree are kept.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${PROFLAME2_SOURCE_REPO:-$(cd "$SCRIPT_DIR/.." && pwd)}"
DEFAULT_CONFIG="esphome/validate_display_preset.yaml"
CONFIG_REL="${1:-$DEFAULT_CONFIG}"
CONFIG_ABS="$REPO_ROOT/$CONFIG_REL"

if [[ ! -f "$CONFIG_ABS" ]]; then
  echo "Config file not found: $CONFIG_ABS" >&2
  exit 1
fi

BUILD_ROOT="${PROFLAME2_BUILD_ROOT:-${TMPDIR:-/tmp}}"
if [[ -n "${PROFLAME2_BUILD_DIR:-}" ]]; then
  BUILD_DIR="$PROFLAME2_BUILD_DIR"
  # mkdir without -p refuses existing paths, including symlinks.
  mkdir -- "$BUILD_DIR"
else
  mkdir -p -- "$BUILD_ROOT"
  BUILD_DIR="$(mktemp -d "$BUILD_ROOT/proflame2-deps.XXXXXX")"
fi
cleanup() {
  rm -rf -- "$BUILD_DIR"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

RSYNC_EXCLUDES=(
  --exclude .git
  --exclude .pytest_cache
  --exclude .ruff_cache
  --exclude .mypy_cache
  --exclude __pycache__
  --exclude .DS_Store
  --exclude analysis
  --exclude hacs_proflame2_work
  --exclude '.venv*'
  --exclude .esphome
  --exclude tmp
  --exclude build
)

echo "Syncing source repo into no-space build workspace..."
rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$REPO_ROOT/" "$BUILD_DIR/"

CONFIG_IN_BUILD="$BUILD_DIR/$CONFIG_REL"
if [[ ! -f "$CONFIG_IN_BUILD" ]]; then
  echo "Synced config file missing: $CONFIG_IN_BUILD" >&2
  exit 1
fi

if [[ -n "${ESPHOME_CLI:-}" ]]; then
  ESPHOME_BIN="$ESPHOME_CLI"
elif [[ -x "$REPO_ROOT/.venv-esphome/bin/esphome" ]]; then
  ESPHOME_BIN="$REPO_ROOT/.venv-esphome/bin/esphome"
elif [[ -x "$REPO_ROOT/.venv/bin/esphome" ]]; then
  ESPHOME_BIN="$REPO_ROOT/.venv/bin/esphome"
elif command -v esphome >/dev/null 2>&1; then
  ESPHOME_BIN="$(command -v esphome)"
else
  echo "Could not find an esphome executable in .venv or PATH." >&2
  exit 1
fi

echo "Using ESPHome binary: $ESPHOME_BIN"
echo "Build workspace: $BUILD_DIR"
echo "Config: $CONFIG_IN_BUILD"

"$ESPHOME_BIN" config "$CONFIG_IN_BUILD"
"$ESPHOME_BIN" compile "$CONFIG_IN_BUILD"

echo
echo "Dependency pre-population complete."
echo "PlatformIO cache: ${PLATFORMIO_CORE_DIR:-${PIO_HOME_DIR:-$HOME/.platformio}}"
echo "Temporary build workspace will be removed on exit."
