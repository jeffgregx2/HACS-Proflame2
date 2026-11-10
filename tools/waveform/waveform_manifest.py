"""Validate private capture metadata and session artifact hashes offline."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMAS = Path(__file__).with_name("waveform_schemas")


def validate_manifest(kind: str, document: dict) -> None:
    """Validate versioned structure and cross-record uniqueness without network IO."""
    if kind not in {"inventory", "session", "trial"}:
        raise ValueError("unsupported waveform manifest kind")
    schema = json.loads((SCHEMAS / f"{kind}.json").read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = list(validator.iter_errors(document))
    if errors:
        # A schema failure can contain private data; report field locations only.
        fields = ["/".join(str(part) for part in error.absolute_path) or "<root>" for error in errors]
        raise ValueError("invalid manifest fields: " + ", ".join(sorted(set(fields))))
    if kind == "inventory":
        aliases = [device["alias"] for device in document["devices"]]
        if len(set(aliases)) != len(aliases):
            raise ValueError("duplicate inventory aliases")
    if kind == "session":
        names = [artifact["filename"] for artifact in document["artifacts"]]
        if len(set(names)) != len(names):
            raise ValueError("duplicate session artifacts")
    if kind == "trial":
        action = document["action_monotonic_ns"]
        if action is not None and action < document["prompt_monotonic_ns"]:
            raise ValueError("trial action precedes its prompt")


def validate_session_files(document: dict, root: Path) -> None:
    """Check bounded-memory hashes; refuse directory traversal and escaping links."""
    validate_manifest("session", document)
    root = root.resolve()
    for artifact in document["artifacts"]:
        path = (root / artifact["filename"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("session artifact is missing or outside session root")
        if path.stat().st_size != artifact["bytes"]:
            raise ValueError("session artifact size mismatch")
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        if digest.hexdigest() != artifact["sha256"]:
            raise ValueError("session artifact checksum mismatch")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("inventory", "session", "trial"))
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--verify-files", action="store_true")
    args = parser.parse_args()
    document = json.loads(args.manifest.read_text())
    validate_manifest(args.kind, document)
    if args.verify_files:
        if args.kind != "session":
            parser.error("--verify-files requires a session manifest")
        validate_session_files(document, args.manifest.parent)
    print("Manifest validation passed")


if __name__ == "__main__":
    main()
