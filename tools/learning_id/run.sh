#!/usr/bin/env bash
set -euo pipefail

# Stage packaging sources too: pip must not leave build metadata in the checkout.
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
work_root="$(mktemp -d /tmp/proflame2-learning-id.XXXXXX)"
child=""
cleanup() {
  if [[ -n "$child" ]]; then
    kill "$child" 2>/dev/null || true
    wait "$child" 2>/dev/null || true
  fi
  rm -rf -- "$work_root"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

run_child() {
  # Explicit stdin forwarding preserves a live JSON pipe for the helper.
  "$@" <&0 &
  child=$!
  local status=0
  wait "$child" || status=$?
  child=""
  return "$status"
}

mkdir -p "$work_root/source/custom_components/proflame2" "$work_root/source/tools" "$work_root/tmp"
cp "$repo_root/pyproject.toml" "$work_root/source/pyproject.toml"
cp -R "$repo_root/custom_components/proflame2/protocol" "$work_root/source/custom_components/proflame2/"
cp -R "$repo_root/tools/learning_id" "$work_root/source/tools/"
export PYTHONDONTWRITEBYTECODE=1 PIP_NO_INPUT=1
export TMPDIR="$work_root/tmp"
printf 'Preparing a temporary Learning ID helper; it will be removed on exit.\n' >&2
run_child "${LEARNING_ID_PYTHON:-python3}" -m venv "$work_root/venv" </dev/null
python="$work_root/venv/bin/python"
run_child "$python" -m pip install --disable-pip-version-check --no-cache-dir --no-deps --quiet \
  "$work_root/source" </dev/null >&2
module=proflame2_learning_id.cli
for argument in "$@"; do
  if [[ "$argument" == "--rf-band" || "$argument" == --rf-band=* ]]; then
    module=proflame2_learning_id.radio
    break
  fi
done
run_child "$python" -m "$module" "$@"
