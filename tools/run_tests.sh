#!/usr/bin/env bash
set -euo pipefail

# Shared checkouts can move between CPU architectures. Build a native Python
# environment for this invocation instead of reusing or replacing .venv.
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
work_root="$(mktemp -d "${TMPDIR:-/tmp}/proflame2-tests.XXXXXX")"
cleanup() {
  rm -rf -- "$work_root"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${TEST_BOOTSTRAP_PYTHON:-python3}" -m venv "$work_root/venv"
python="$work_root/venv/bin/python"
export PATH="$work_root/venv/bin:$PATH"
export PYTHONDONTWRITEBYTECODE=1
export COVERAGE_FILE="$work_root/.coverage"
"$python" -m pip install --disable-pip-version-check --no-cache-dir -r "$repo_root/requirements-dev.txt"
cd -- "$repo_root"
"$python" -m pytest -q -o "cache_dir=$work_root/pytest-cache" --cov-report=term-missing --cov-report="xml:$work_root/coverage.xml" "$@"
