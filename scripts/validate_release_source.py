#!/usr/bin/env python3
"""Validate the repository content that will be published under a release tag."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

RELEASE_TAG_PATTERN = re.compile(r"v(?P<version>\d+\.\d+\.\d+(?:-beta\d+)?)$")


def parse_release_tag(tag: str) -> str:
    """Return the version represented by a supported release tag."""

    match = RELEASE_TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError(f"Invalid release tag {tag!r}. Use vX.Y.Z or vX.Y.Z-betaN.")
    return match.group("version")


def _load_stamp_docs_ref_module():
    script_path = Path("scripts/stamp_docs_ref.py")
    spec = importlib.util.spec_from_file_location("stamp_docs_ref", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def validate_release_source(tag: str, prerelease: bool | None = None) -> None:
    """Validate versions and documentation links for the supplied release tag."""

    version = parse_release_tag(tag)
    is_beta = "-beta" in version
    if is_beta and prerelease is False:
        raise ValueError(f"Beta release tag {tag!r} must be marked as a GitHub prerelease.")

    manifest_path = Path("custom_components/proflame2/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_version = manifest.get("version")
    if manifest_version != version:
        raise ValueError(f"manifest.json version mismatch: expected {version!r}, found {manifest_version!r}.")

    version_path = Path("custom_components/proflame2/version.py")
    version_text = version_path.read_text(encoding="utf-8")
    version_match = re.search(
        r'^DEFAULT_INTEGRATION_VERSION = "(?P<version>[^"]+)"$',
        version_text,
        flags=re.MULTILINE,
    )
    if version_match is None:
        raise ValueError("DEFAULT_INTEGRATION_VERSION not found in version.py.")
    if version_match.group("version") != version:
        raise ValueError("version.py mismatch: " f"expected {version!r}, found {version_match.group('version')!r}.")

    _load_stamp_docs_ref_module().validate_docs_ref(tag)


def main() -> None:
    """Run release-source validation from the repository root."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Release tag, for example v0.6.0-beta3.")
    parser.add_argument(
        "--prerelease",
        choices=("true", "false"),
        help="Validate the GitHub prerelease setting when it is known.",
    )
    args = parser.parse_args()

    try:
        prerelease = None if args.prerelease is None else args.prerelease == "true"
        validate_release_source(args.tag, prerelease)
    except ValueError as err:
        raise SystemExit(str(err)) from err

    print(f"Release source for {args.tag} is valid.")


if __name__ == "__main__":
    main()
