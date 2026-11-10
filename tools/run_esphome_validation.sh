#!/usr/bin/env bash
set -euo pipefail

# Each invocation owns its stage. Never delete another build's workspace.
work_root="$(mktemp -d "${TMPDIR:-/tmp}/proflame2-esphome.XXXXXX")"
cleanup() {
  rm -rf -- "$work_root"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${MAKE:-make}" --no-print-directory "ESPHOME_WORK_ROOT=$work_root" "$@"
