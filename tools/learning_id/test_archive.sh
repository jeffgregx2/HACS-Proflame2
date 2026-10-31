#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
work_root="$(mktemp -d /tmp/proflame2-archive-tests.XXXXXX)"
trap 'rm -rf -- "$work_root"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TMPDIR="$work_root"
echo "Creating temporary archive-test environment (limit: 120 seconds)..."
"${TEST_BOOTSTRAP_PYTHON:-python3}" -c \
  'import subprocess, sys; subprocess.run([sys.executable, "-m", "venv", sys.argv[1]], check=True, timeout=120)' \
  "$work_root/tests"
python="$work_root/tests/bin/python"
echo "Installing archive-test dependencies (limit: 180 seconds)..."
"$python" -c \
  'import subprocess, sys; subprocess.run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--timeout", "30", "--retries", "2", "pytest>=8,<10"], check=True, timeout=180)'
echo "Running archive checks..."
"$python" -B "$repo_root/tools/learning_id/validate_archive.py" "$@"
