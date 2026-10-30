"""Single-file distribution entry point; retain the existing collectors."""

from __future__ import annotations

import json
import sys
from importlib.resources import files

from . import cli, radio


def main(argv: list[str] | None = None) -> int:
    """Validate all arguments before choosing file, pipe, or managed reception."""
    arguments = sys.argv[1:] if argv is None else argv
    parser = cli.build_parser()
    parser.prog = "proflame2-learning-id.pyz"
    parser.description = "Generate a Proflame2 Learning ID on Linux/macOS without installing the helper."
    parser.epilog = (
        "Examples:\n"
        "  python3 proflame2-learning-id.pyz --rf-band 315 --timeout 600\n"
        "  python3 proflame2-learning-id.pyz remote-capture.json -v"
    )
    parser.add_argument("--rf-band", choices=("315", "433"), help="Start rtl_433 on this band; omit for JSON input.")
    parser.add_argument("--rtl433", help="Compatible rtl_433 executable path; use with --rf-band.")
    try:
        info = json.loads(files("proflame2_learning_id").joinpath("BUILD_INFO.json").read_text(encoding="utf-8"))
        version = f"{info['version']} source={info['source_commit']} dirty={info['source_dirty']}"
    except FileNotFoundError:
        version = "development source (no archive build information)"
    parser.add_argument("--version", action="version", version=version)
    args = parser.parse_args(arguments)
    if not arguments:
        parser.print_help()
        return 0
    cli.validate_timeout(parser, args.timeout)
    if args.rf_band:
        if args.file is not None or args.input_mode == "file":
            parser.error("--rf-band starts live reception and cannot be combined with saved-file input.")
        return radio.main(arguments)
    if args.rtl433:
        parser.error("--rtl433 requires --rf-band.")
    return cli.main(arguments)
