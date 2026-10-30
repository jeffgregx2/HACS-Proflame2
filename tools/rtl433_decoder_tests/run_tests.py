"""Delegate C decoder regression checks to their authoritative rtl_433 checkout."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument("--source", type=Path, required=True)
    args, remaining = parser.parse_known_args()
    runner = args.source.resolve() / "tests/proflame2/test_decoder.py"
    if not runner.is_file():
        parser.error(f"rtl_433 checkout does not contain its regression runner: {runner}")
    return subprocess.run(
        [sys.executable, str(runner), "--source", str(args.source.resolve()), *remaining], check=False
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
