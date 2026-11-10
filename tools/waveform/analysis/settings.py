"""Keep investigation-specific dataset names outside reusable source."""

import json
import os
from pathlib import Path

from tools.waveform.environment import load_environment


def datasets():
    load_environment()
    path = os.environ.get("PROFLAME2_ANALYSIS_DATASETS")
    if not path:
        raise ValueError("set PROFLAME2_ANALYSIS_DATASETS to an external JSON dataset mapping")
    mapping = json.loads(Path(path).expanduser().read_text())
    if set(mapping) != {"native", "baseline", "candidate"}:
        raise ValueError("dataset roles must be native, baseline, and candidate")
    if any(not isinstance(name, str) or Path(name).name != name or name in {".", ".."} for name in mapping.values()):
        raise ValueError("dataset values must be run directory names, not paths")
    return mapping
