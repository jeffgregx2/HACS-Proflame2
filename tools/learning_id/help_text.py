"""Inspection instructions shared by all helper entry points."""

import argparse

INSPECTION_HELP = """If you wish to examine the contents of this archive before running it:
  The .pyz is a ZIP of readable Python source, not a bundled interpreter.
  These commands inspect or extract it without executing the helper:

  unzip -l proflame2-learning-id.pyz
  unzip -p proflame2-learning-id.pyz __main__.py
  unzip proflame2-learning-id.pyz -d proflame2-learning-id-source

  Without unzip, Python's standard ZIP tool also inspects without running it:
  python3 -m zipfile -l proflame2-learning-id.pyz
  python3 -m zipfile -e proflame2-learning-id.pyz proflame2-learning-id-source

  Review __main__.py, README.md, proflame2_learning_id/, and
  proflame2_protocol/. Build information is in
  proflame2_learning_id/BUILD_INFO.json; LICENSE contains the license.
  After reviewing, run either the archive or the extracted source:

  python3 proflame2-learning-id.pyz --rf-band 315 --timeout 600
  python3 proflame2-learning-id-source/__main__.py --rf-band 315 --timeout 600

  Python 3.10+ is required. Install and configure your own SDR and compatible
  rtl_433 first. The helper installs nothing and saves no configuration.
  Delete the downloaded archive and any extracted review directory afterward.
  This flag runs the helper only to display these instructions, then exits.
  To inspect without executing any helper code, use the ZIP commands above.
"""


def add_inspection_option(parser: argparse.ArgumentParser) -> None:
    """Use argparse's print-and-exit action before any input or receiver work."""
    parser.add_argument(
        "--inspect-pyz-archive",
        action="version",
        version=INSPECTION_HELP,
        help="Show optional archive inspection instructions and exit.",
    )
