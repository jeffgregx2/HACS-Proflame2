"""Load private waveform settings without evaluating shell commands."""

from __future__ import annotations

import argparse
import os
import re
import shlex
import stat
from pathlib import Path


def config_directory() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "proflame2"


def capture_root() -> Path:
    default = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "proflame2/waveform"
    return Path(os.environ.get("PROFLAME2_CAPTURE_ROOT", default)).expanduser()


def load_environment(path: Path | None = None) -> None:
    explicit = path is not None or "PROFLAME2_WAVEFORM_ENV" in os.environ
    path = path or Path(os.environ.get("PROFLAME2_WAVEFORM_ENV", config_directory() / "waveform.env"))
    path = path.expanduser().resolve()
    repository = Path(__file__).resolve().parents[2]
    if path.is_relative_to(repository) or any((parent / ".git").exists() for parent in path.parents):
        raise ValueError("private waveform configuration must be outside the source checkout")
    if not path.exists() and not explicit:
        return
    with path.open() as source:
        mode = stat.S_IMODE(os.fstat(source.fileno()).st_mode)
        if mode & 0o077:
            raise ValueError("private waveform configuration requires owner-only permissions (0600)")
        settings = {}
        for number, line in enumerate(source, 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            key, separator, value = line.strip().partition("=")
            if not separator or not re.fullmatch(r"PROFLAME2_[A-Z0-9_]+", key):
                raise ValueError(f"invalid waveform configuration key on line {number}")
            values = shlex.split(value, comments=True)
            if len(values) != 1:
                raise ValueError(f"invalid waveform configuration value on line {number}")
            # Values are literal: no shell execution or variable interpolation.
            settings[key] = values[0]
    for key, value in settings.items():
        os.environ.setdefault(key, value)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    try:
        load_environment()
    except (OSError, ValueError):
        parser.exit(2, "Private waveform configuration is missing, unsafe, or invalid.\n")
    os.execvp(command[0], command)


if __name__ == "__main__":
    main()
