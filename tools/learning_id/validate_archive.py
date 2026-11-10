"""Test the actual source archive with a dependency-free runtime interpreter."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Test this existing archive rather than a temporary build.")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="proflame2-archive-check.") as directory:
        work = Path(directory)
        archive = args.archive.resolve() if args.archive else work / "package/proflame2-learning-id.pyz"
        if not args.archive:
            subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "tools/learning_id/build_archive.py"),
                    "--output-dir",
                    str(archive.parent),
                ],
                check=True,
                timeout=60,
            )
        runtime = work / "runtime"
        print("Creating dependency-free helper runtime...", flush=True)
        venv.create(runtime, with_pip=False, symlinks=True)
        python = runtime / "bin/python"
        config = work / "pytest.ini"
        config.write_text("[pytest]\n", encoding="utf-8")
        env = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PROFLAME2_HELPER_ARCHIVE": str(archive),
            "PROFLAME2_HELPER_PYTHON": str(python),
        }
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-c",
                str(config),
                "-o",
                "addopts=",
                "-p",
                "no:cacheprovider",
                "-vv",
                "-o",
                "faulthandler_timeout=60",
                str(ROOT / "tools/learning_id/tests/test_archive.py"),
            ],
            check=True,
            cwd=directory,
            env=env,
            timeout=300,
        )
    print("Source-archive tests passed with no installed helper, pip, Home Assistant, or rtl_433.")


if __name__ == "__main__":
    main()
