"""Relocate legacy capture evidence only after byte-for-byte copy verification."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
from pathlib import Path


def inventory(root: Path) -> dict[str, tuple]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("migration refuses links and special files")
        relative = str(path.relative_to(root))
        if path.is_dir():
            result[relative] = ("directory",)
        else:
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            result[relative] = ("file", path.stat().st_size, digest)
    return result


def relocate(source: Path, destination: Path) -> None:
    if source.is_symlink() or not source.is_dir() or source.stat().st_uid != os.getuid():
        raise ValueError("migration requires an owned, inactive capture directory")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("migration destination already exists; refusing to merge evidence")
    before = inventory(source)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".capture-copy-", dir=destination.parent) as staging:
        copy = Path(staging) / "evidence"
        shutil.copytree(source, copy)
        if inventory(copy) != before or inventory(source) != before:
            raise ValueError("migration verification failed or source changed; originals retained")
        if destination.exists():
            raise FileExistsError("migration destination appeared during copy")
        copy.rename(destination)
    # Only the explicitly verified source is removed, never a broad /tmp sweep.
    shutil.rmtree(source)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    relocate(args.source, args.destination)
    print(f"Verified copy completed; original removed: {args.source.name}", flush=True)


if __name__ == "__main__":
    main()
