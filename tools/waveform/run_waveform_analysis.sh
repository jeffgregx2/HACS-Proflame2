#!/usr/bin/env bash
# Offline only: build the reviewed detector in owned scratch, match, and slice.
set -euo pipefail

# Re-enter once through the safe environment loader; never source shell code.
root="$(dirname "$(dirname "$(dirname "$(readlink -f "$0")")")")"
export PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1
if [[ "${PROFLAME2_ENV_LOADED:-0}" != 1 ]]; then
  export PROFLAME2_ENV_LOADED=1
  exec "${PYTHON:-python3}" -B -m tools.waveform.environment -- bash "$0" "$@"
fi

if [[ $# -lt 4 || $# -gt 5 ]]; then
  printf 'Usage: %s recording.cu8 receiver.log sample_rate_hz output_directory [fixed_level_db]\n' "$0" >&2
  exit 2
fi
: "${PROFLAME2_RTL433_SOURCE:?Set PROFLAME2_RTL433_SOURCE to the reviewed rtl_433 source tree}"
scratch="$(mktemp -d /tmp/proflame2-waveform-analysis.XXXXXX)"
trap 'rm -r -- "$scratch"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${CMAKE:-cmake}" -S "$root/tools/waveform/waveform_rtl433" -B "$scratch/build" \
  -DRTL433_SOURCE="$PROFLAME2_RTL433_SOURCE"
"${CMAKE:-cmake}" --build "$scratch/build" --target waveform_rtl433 -j 4

extra=()
if [[ $# -eq 5 ]]; then
  extra=(--fixed-level-db "$5")
fi
"${PYTHON:-python3}" -m tools.waveform.waveform_match "$1" "$2" --sample-rate "$3" \
  --detector "$scratch/build/waveform_rtl433" --output "$4" "${extra[@]}"
