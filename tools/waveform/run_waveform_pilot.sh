#!/usr/bin/env bash
# Resolve source independently of the caller's working directory.
set -euo pipefail
root="$(dirname "$(dirname "$(dirname "$(readlink -f "$0")")")")"
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
exec "${PYTHON:-python3}" -B -u -m tools.waveform.waveform_pilot "$@"
