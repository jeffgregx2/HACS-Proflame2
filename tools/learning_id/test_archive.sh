#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
work_root="$(mktemp -d /tmp/proflame2-archive-tests.XXXXXX)"
trap 'rm -rf -- "$work_root"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export PYTHONDONTWRITEBYTECODE=1 TMPDIR="$work_root"
"${TEST_BOOTSTRAP_PYTHON:-python3}" -m venv "$work_root/tests"
python="$work_root/tests/bin/python"
"$python" -m pip install --disable-pip-version-check --no-cache-dir --quiet 'pytest>=8,<10'
"$python" -B "$repo_root/tools/learning_id/validate_archive.py" "$@"
