"""Build in scratch space and verify an installed wheel in a clean environment.

Run with a development venv containing build/setuptools/wheel/pytest. This
command does not generate build metadata in either source checkout.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    """Validate packaging without HA, rtl_433, or source import manipulation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ha-interoperability",
        action="store_true",
        help="Also exercise existing HA import adapters in the development environment.",
    )
    args = parser.parse_args()
    with TemporaryDirectory(prefix="proflame2-package.") as temp:
        workspace = Path(temp)
        source = workspace / "source"
        source.mkdir()
        shutil.copy2(ROOT / "pyproject.toml", source / "pyproject.toml")
        for name in ("custom_components/proflame2/protocol", "tools/learning_id"):
            shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        subprocess.run(
            [
                sys.executable,
                "-m",
                "build",
                "--wheel",
                "--no-isolation",
                str(source),
                "--outdir",
                str(workspace / "dist"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        wheel = next((workspace / "dist").glob("*.whl"))
        environment = workspace / "clean"
        venv.create(environment, with_pip=True)
        python = environment / "bin" / "python"
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", "--no-index", str(wheel)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import importlib.util, proflame2_protocol, proflame2_learning_id.cli; "
                "assert importlib.util.find_spec('homeassistant') is None; "
                "assert importlib.util.find_spec('custom_components') is None",
            ],
            check=True,
            cwd=temp,
        )
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PROFLAME2_HELPER_PYTHON": str(python)}
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-o",
                "addopts=",
                "-p",
                "no:cacheprovider",
                "-q",
                str(ROOT / "tools/learning_id/tests"),
            ],
            check=True,
            env=env,
            cwd=temp,
        )
        print("Installed-wheel CLI tests passed in a clean environment without Home Assistant or rtl_433.")
        if args.ha_interoperability:
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "--no-cov",
                    "-p",
                    "no:cacheprovider",
                    "-q",
                    "tests/test_rtl433_learning.py",
                ],
                check=True,
                env=env,
                cwd=ROOT,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
