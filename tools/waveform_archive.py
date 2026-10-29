"""Package and verify private waveform sessions without extracting archive members."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import signal
import tarfile
import tempfile
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from tools.waveform_diagnostics import read_windows
from tools.waveform_manifest import validate_manifest

MAX_METADATA = 4 * 1024 * 1024
MAX_METADATA_TOTAL = 32 * 1024 * 1024
MAX_FILES = 4096
MAX_TOTAL_BYTES = 64 * 1024**3


class Progress:
    """Report phases immediately and byte progress at most every 30 seconds."""

    def __init__(self, phase: str, status: Callable[[str], None] | None, total: int | None = None):
        self.phase, self.status, self.total = phase, status, total
        self.count = 0
        self.started = self.last = time.monotonic()
        if status:
            status(f"{phase}: starting...")

    def advance(self, count: int) -> None:
        self.count += count
        now = time.monotonic()
        if self.status and now - self.last >= 30:
            self.report(now)

    def report(self, now: float, *, done: bool = False) -> None:
        if self.status:
            amount = f"{self.count / 1024**2:.1f} MiB"
            if self.total is not None:
                amount += f" / {self.total / 1024**2:.1f} MiB"
                if self.total:
                    amount += f" ({min(100, self.count * 100 / self.total):.0f}%)"
            self.status(f"{self.phase}: {amount}; elapsed {now - self.started:.0f}s" + ("; done" if done else ""))
        self.last = now


class ProgressReader:
    def __init__(self, source, progress: Progress):
        self.source, self.progress = source, progress

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(size)
        self.progress.advance(len(data))
        return data


def safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or "\\" in name or ".." in path.parts or str(path) != name or name == ".":
        raise ValueError("unsafe or noncanonical session path")


def metadata(data: bytes) -> dict:
    if len(data) > MAX_METADATA:
        raise ValueError("metadata exceeds bounded size")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate metadata key")
            result[key] = value
        return result

    value = json.loads(data, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    return value


def validate_records(
    session: dict, documents: dict[str, dict], actual: dict[str, tuple[int, str]], valid_receiver_logs: set[str]
) -> None:
    validate_manifest("session", session)
    expected = {entry["filename"]: (entry["bytes"], entry["sha256"]) for entry in session["artifacts"]}
    for name in expected:
        safe_name(name)
    if "session.json" in expected or expected != actual:
        raise ValueError("session artifact set, size or checksum mismatch")
    inventory = documents.get("inventory.json")
    if inventory is None:
        raise ValueError("inventory artifact missing")
    validate_manifest("inventory", inventory)
    if actual["inventory.json"][1] != session["inventory_sha256"]:
        raise ValueError("inventory checksum differs from session")
    roles = {device["alias"]: device["role"] for device in inventory["devices"]}
    if roles.get(session["receiver_alias"]) != "receiver" or roles.get(session["source_alias"]) not in {
        "native",
        "transmitter",
    }:
        raise ValueError("session aliases do not match inventory roles")
    found = {}
    for name, trial in documents.items():
        if not name.endswith("/trial.json"):
            continue
        validate_manifest("trial", trial)
        identity = trial["trial_id"]
        if identity in found or name != f"{identity}/trial.json" or trial["source_alias"] != session["source_alias"]:
            raise ValueError("trial identity/source mismatch")
        found[identity] = trial
    if not set(session["trials"]).issubset(found):
        raise ValueError("listed trial artifact missing")
    if session["status"] == "complete" and (not found or set(found) != set(session["trials"])):
        raise ValueError("complete session has missing or unlisted trials")
    # Failed/aborted trials can exist without joining the completed trial list.
    if session["status"] == "complete":
        for identity in session["trials"]:
            acquisition = documents.get(f"{identity}/acquisition.json", {})
            if acquisition.get("status") != "complete" or "incomplete_acquisition" in found[identity]["quality_flags"]:
                raise ValueError("complete session contains incomplete acquisition")
            for suffix in ("signal.cu8", "receiver.stdout", "receiver.stderr", "sdr.stdout", "sdr.stderr"):
                name = f"{identity}/{suffix}"
                if name not in actual:
                    raise ValueError("required recording artifact missing")
            size = actual[f"{identity}/signal.cu8"][0]
            if not size or size % 2 or acquisition.get("iq_samples") != size // 2:
                raise ValueError("IQ sample count/size mismatch")
            if f"{identity}/receiver.stdout" not in valid_receiver_logs:
                raise ValueError("complete acquisition has incomplete receiver session")


def check_receiver(data: bytes) -> bool:
    try:
        read_windows(data.decode(errors="replace").splitlines(), require_complete_sessions=True)
    except ValueError:
        return False
    return True


def digest_stream(stream) -> tuple[int, str]:
    digest = hashlib.sha256()
    count = 0
    while block := stream.read(1024 * 1024):
        count += len(block)
        digest.update(block)
    return count, digest.hexdigest()


def inspect_directory(root: Path, *, status: Callable[[str], None] | None = None) -> dict:
    progress = Progress("Checking source files", status)
    root = root.resolve(strict=True)
    actual = {}
    documents = {}
    valid_receiver_logs = set()
    total = 0
    metadata_total = 0
    for index, path in enumerate(root.rglob("*"), 1):
        if index > MAX_FILES * 2:
            raise ValueError("session directory exceeds entry limit")
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise ValueError("session contains link or special file")
        if path.is_dir():
            continue
        name = path.relative_to(root).as_posix()
        safe_name(name)
        total += path.stat().st_size
        if len(actual) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            raise ValueError("session exceeds archive resource limits")
        with path.open("rb") as source:
            if path.suffix == ".json":
                metadata_total += path.stat().st_size
                if metadata_total > MAX_METADATA_TOTAL:
                    raise ValueError("session metadata exceeds aggregate limit")
                documents[name] = metadata(source.read(MAX_METADATA + 1))
                source.seek(0)
            elif name.endswith("/receiver.stdout") and path.stat().st_size <= 32 * 1024 * 1024:
                if check_receiver(source.read()):
                    valid_receiver_logs.add(name)
                source.seek(0)
            if name != "session.json":
                actual[name] = digest_stream(ProgressReader(source, progress))
    if "session.json" not in documents:
        raise ValueError("session manifest missing")
    session = documents["session.json"]
    validate_records(session, documents, actual, valid_receiver_logs)
    progress.report(time.monotonic(), done=True)
    return session


def verify_archive(path: Path, *, status: Callable[[str], None] | None = None, total_bytes: int | None = None) -> dict:
    progress = Progress("Verifying archive", status, total_bytes)
    actual = {}
    documents = {}
    valid_receiver_logs = set()
    seen = set()
    total = 0
    metadata_total = 0
    with gzip.open(path, "rb") as compressed, tarfile.open(fileobj=compressed, mode="r|") as archive:
        for member in archive:
            safe_name(member.name)
            if not member.isfile() or member.name in seen or member.size < 0:
                raise ValueError("archive contains duplicate, link or special member")
            seen.add(member.name)
            total += member.size
            if len(seen) > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise ValueError("archive exceeds resource limits")
            source = ProgressReader(archive.extractfile(member), progress)
            if member.name.endswith(".json"):
                metadata_total += member.size
                if metadata_total > MAX_METADATA_TOTAL:
                    raise ValueError("archive metadata exceeds aggregate limit")
                data = source.read(MAX_METADATA + 1)
                documents[member.name] = metadata(data)
                count, digest = len(data), hashlib.sha256(data).hexdigest()
            elif member.name.endswith("/receiver.stdout") and member.size <= 32 * 1024 * 1024:
                data = source.read()
                if check_receiver(data):
                    valid_receiver_logs.add(member.name)
                count, digest = len(data), hashlib.sha256(data).hexdigest()
            else:
                count, digest = digest_stream(source)
            if count != member.size:
                raise ValueError("truncated archive member")
            if member.name != "session.json":
                actual[member.name] = (count, digest)
        # Read through the gzip trailer as well: tar's end marker alone is not
        # proof that the compressed file was fully transferred without damage.
        trailing = 0
        while block := compressed.read(1024 * 1024):
            trailing += len(block)
            if trailing > MAX_METADATA or any(block):
                raise ValueError("unexpected trailing archive content")
    if "session.json" not in documents:
        raise ValueError("archive session manifest missing")
    session = documents["session.json"]
    validate_records(session, documents, actual, valid_receiver_logs)
    progress.report(time.monotonic(), done=True)
    return session


def pack_session(root: Path, destination: Path, *, status: Callable[[str], None] | None = None) -> dict:
    session = inspect_directory(root, status=status)
    root = root.resolve()
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("archive destination exists")
    if destination.is_relative_to(root):
        raise ValueError("archive destination must be outside session directory")
    total = (root / "session.json").stat().st_size + sum(entry["bytes"] for entry in session["artifacts"])
    progress = Progress("Compressing recordings", status, total)
    with tempfile.TemporaryDirectory(prefix="proflame2-archive-", dir=destination.parent) as scratch:
        temporary = Path(scratch) / "session.tar.gz"
        # Raw IQ compresses expensively at gzip's default level; use fast,
        # lossless compression without changing any captured sample bytes.
        with tarfile.open(temporary, "w:gz", format=tarfile.USTAR_FORMAT, compresslevel=1) as archive:
            for name in ["session.json", *(entry["filename"] for entry in session["artifacts"])]:
                path = root / name
                if path.is_symlink() or not path.is_file():
                    raise ValueError("session changed during packaging")
                info = tarfile.TarInfo(name)
                info.size = path.stat().st_size
                info.mode = 0o600
                # TarInfo defaults omit owner names, IDs and host timestamps.
                with path.open("rb") as source:
                    archive.addfile(info, ProgressReader(source, progress))
        progress.report(time.monotonic(), done=True)
        verified = verify_archive(temporary, status=status, total_bytes=total)
        if verified != session:
            raise ValueError("session manifest changed during packaging")
        temporary.chmod(0o600)
        os.link(temporary, destination)  # Publish atomically without overwriting.
    return session


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pack = commands.add_parser("pack")
    pack.add_argument("session", type=Path)
    pack.add_argument("archive", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("archive", type=Path)
    args = parser.parse_args()

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:

        def status(message):
            print(message, flush=True)

        session = (
            pack_session(args.session, args.archive, status=status)
            if args.command == "pack"
            else verify_archive(args.archive, status=status)
        )
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(f"Archive verified; acquisition status={session['status']}. Calibration is not established by this check.")


if __name__ == "__main__":
    main()
