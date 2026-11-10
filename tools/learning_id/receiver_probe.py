"""Offline receiver capability checks using synthetic, non-identifying PCM.

rtl_433's -y input invokes the C decoder without opening an SDR. Frozen vectors
come from its Proflame2 regression goldens, not the Python encoder under test.
"""

from __future__ import annotations

import json
import os
import subprocess

from proflame2_protocol.rtl433 import normalize_rtl433_record

PROBES = (
    ("legacy", "{182}e5659ab96995ae66695b965596e59696b959956e9aa568", "123456212614bc", "legacy_7_word"),
    (
        "extended",
        "{260}e5659ab96995ae66695ba55596e59556b95999aea5956b955556e59565b9a6a5a0",
        "123456812015c800216e",
        "extended_10_word",
    ),
    (
        "extended terminal",
        "{259}e5659ab96995ae66695ba55596e59556b95999aea5956b955556e59565b9a6a5a0",
        "123456812015c800216e",
        "extended_10_word",
    ),
)


class ReceiverCompatibilityError(ValueError):
    """The executable did not demonstrate the required JSON/decoder contract."""


def no_core_dump():
    # Old decoders may crash on extended input; never leave a core in the user's
    # working directory during a compatibility check.
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def check(executable: str) -> str:
    """Require legacy, extended, and terminal-bit decoding, not a version number."""
    try:
        version = subprocess.run([executable, "-V"], capture_output=True, text=True, timeout=5, check=False)
        if version.returncode:
            raise ReceiverCompatibilityError("The rtl_433 version check failed.")
        for name, pcm, raw_words, variant in PROBES:
            result = subprocess.run(
                [executable, "-c", "0", "-R", "207", "-y", pcm, "-F", "json"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
                preexec_fn=no_core_dump if os.name == "posix" else None,
            )
            try:
                records = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
                frames = [normalize_rtl433_record(row) for row in records]
            except (ValueError, TypeError, RecursionError):
                raise ReceiverCompatibilityError(f"The {name} offline check did not produce compatible JSON.") from None
            valid = [frame for frame in frames if frame is not None]
            if (
                result.returncode
                or len(valid) != 1
                or bytes(valid[0].frame.wire_words).hex() != raw_words
                or not any(
                    row.get("protocol_variant") == variant and row.get("raw_words") == raw_words for row in records
                )
            ):
                raise ReceiverCompatibilityError(f"The {name} offline decoding check failed.")
    except OSError:
        raise ReceiverCompatibilityError("The rtl_433 executable could not be started.") from None
    except subprocess.TimeoutExpired:
        raise ReceiverCompatibilityError("An rtl_433 offline check timed out.") from None
    lines = (version.stdout + "\n" + version.stderr).splitlines()
    return next((line[:300] for line in lines if line.startswith("rtl_433 version")), "rtl_433 (version not reported)")
