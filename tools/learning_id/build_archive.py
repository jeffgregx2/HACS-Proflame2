"""Build a source-only zipapp and checksum from the current working files."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER_MODULES = ("__init__", "archive", "cli", "evidence", "guided", "help_text", "radio", "receiver_probe")
ENTRY_POINT = '''"""Readable source-only Proflame2 Learning ID entry point."""
import sys

if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required; nothing has been installed.")

sys.dont_write_bytecode = True
from proflame2_learning_id.archive import main

raise SystemExit(main())
'''


def build(output: Path, *, require_clean: bool = False) -> tuple[Path, Path]:
    """Package only allowlisted source, neutral build metadata, and the license."""
    output = output.resolve()
    if output.is_relative_to(ROOT):
        raise ValueError("Choose an output directory outside the source checkout, such as an owned /tmp directory.")
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL)
        )
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    if require_clean and (commit is None or dirty is not False):
        raise ValueError("Release packaging requires a clean Git checkout with a known source commit.")
    entries = {"__main__.py": ENTRY_POINT.encode(), "LICENSE": (ROOT / "LICENSE").read_bytes()}
    helper = ROOT / "tools/learning_id"
    entries["README.md"] = (helper / "archive_README.md").read_bytes()
    for name in HELPER_MODULES:
        entries[f"proflame2_learning_id/{name}.py"] = (helper / f"{name}.py").read_bytes()
    for source in sorted((ROOT / "custom_components/proflame2/protocol").glob("*.py")):
        entries[f"proflame2_protocol/{source.name}"] = source.read_bytes()
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(entries.items())}
    manifest = json.loads((ROOT / "custom_components/proflame2/manifest.json").read_text(encoding="utf-8"))
    info = dict(
        version=manifest["version"],
        source_commit=commit,
        source_dirty=dirty,
        content_sha256=hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest(),
    )
    entries["proflame2_learning_id/BUILD_INFO.json"] = (json.dumps(info, indent=2) + "\n").encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(entries.items()):
            member = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            member.create_system = 3
            member.external_attr = 0o100644 << 16
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, data)
    payload = buffer.getvalue()
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / "proflame2-learning-id.pyz"
    checksum_path = output / "proflame2-learning-id.pyz.sha256"
    if archive_path.exists() or checksum_path.exists():
        raise ValueError("Package output already exists; choose a new output directory.")
    owned = []
    try:
        for path, data in (
            (archive_path, payload),
            (checksum_path, f"{hashlib.sha256(payload).hexdigest()}  {archive_path.name}\n".encode()),
        ):
            with path.open("xb") as stream:
                owned.append(path)
                stream.write(data)
    except BaseException:
        for path in owned:
            path.unlink(missing_ok=True)
        raise
    return archive_path, checksum_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True, help="Outside the checkout; retained for your use.")
    parser.add_argument("--require-clean", action="store_true", help="Refuse dirty or unknown source provenance.")
    args = parser.parse_args()
    try:
        paths = build(args.output_dir, require_clean=args.require_clean)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Unable to build Learning ID archive: {exc}\n")
    for path in paths:
        print(path.name)


if __name__ == "__main__":
    main()
