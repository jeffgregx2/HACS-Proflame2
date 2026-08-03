#!/usr/bin/env python3
"""Stamp or validate repository documentation URLs for a branch or release tag."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
from pathlib import Path

DOCS_URLS_PATH = Path("custom_components/proflame2/docs_urls.py")


def _load_docs_urls_module():
    spec = importlib.util.spec_from_file_location("proflame2_docs_urls", DOCS_URLS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {DOCS_URLS_PATH}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def documentation_url(documentation_ref: str) -> str:
    """Return the manifest documentation URL for a repository ref."""

    docs_urls = _load_docs_urls_module()
    return (
        f"{docs_urls.REPOSITORY_URL}/blob/{documentation_ref}/"
        f"{docs_urls.ADD_FIREPLACE_PROFILE_DOC}#{docs_urls.ADD_FIREPLACE_PROFILE_OPTIONS_ANCHOR}"
    )


def stamp_docs_ref(documentation_ref: str) -> None:
    """Stamp manifest.json and docs_urls.py with the supplied documentation ref."""

    manifest_path = Path("custom_components/proflame2/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["documentation"] = documentation_url(documentation_ref)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    docs_urls_text = DOCS_URLS_PATH.read_text(encoding="utf-8")
    docs_urls_text, replacements = re.subn(
        r'^DEFAULT_DOCUMENTATION_REF = "[^"]+"$',
        f'DEFAULT_DOCUMENTATION_REF = "{documentation_ref}"',
        docs_urls_text,
        count=1,
        flags=re.MULTILINE,
    )
    if replacements != 1:
        raise RuntimeError("DEFAULT_DOCUMENTATION_REF not found in docs_urls.py")

    DOCS_URLS_PATH.write_text(docs_urls_text, encoding="utf-8")


def validate_docs_ref(documentation_ref: str) -> None:
    """Validate manifest.json and docs_urls.py against the supplied documentation ref."""

    manifest_path = Path("custom_components/proflame2/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_documentation = documentation_url(documentation_ref)
    actual_documentation = manifest.get("documentation")
    if actual_documentation != expected_documentation:
        raise SystemExit(
            "manifest.json documentation URL mismatch: "
            f"expected {expected_documentation!r}, found {actual_documentation!r}."
        )

    docs_urls_text = DOCS_URLS_PATH.read_text(encoding="utf-8")
    docs_ref_match = re.search(
        r'^DEFAULT_DOCUMENTATION_REF = "(?P<ref>[^"]+)"$',
        docs_urls_text,
        flags=re.MULTILINE,
    )
    if docs_ref_match is None:
        raise SystemExit("DEFAULT_DOCUMENTATION_REF not found in docs_urls.py.")

    actual_ref = docs_ref_match.group("ref")
    if actual_ref != documentation_ref:
        raise SystemExit(
            f"docs_urls.py documentation ref mismatch: expected {documentation_ref!r}, found {actual_ref!r}."
        )


def main() -> None:
    """Run the docs-ref stamping command."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate the current files instead of modifying them.",
    )
    parser.add_argument(
        "--ref",
        required=True,
        help="GitHub branch or tag that documentation URLs should target.",
    )
    args = parser.parse_args()

    if args.check:
        validate_docs_ref(args.ref)
    else:
        stamp_docs_ref(args.ref)


if __name__ == "__main__":
    main()
