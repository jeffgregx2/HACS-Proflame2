"""Deterministic synthetic RF windows, never recordings from a real device."""

from itertools import groupby

from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.waveform import frame_to_air_bytes
from tools.waveform_diagnostics import checksum


def power_fixture():
    captures = []
    for identity, cmd1 in ((2, 1), (4, 0)):
        frame = ProflameFrame(serial_id=0x123456, cmd1=cmd1, cmd2=6, err1=0x76 if cmd1 else 0x57, err2=0xDE)
        bits = "".join(f"{byte:08b}" for byte in frame_to_air_bytes(frame))
        runs = [(int(bit), len(list(group)) * 417) for bit, group in groupby("1010000000000000" + bits)]
        words = []
        for index in range(0, len(runs), 2):
            level, duration = runs[index]
            word = duration | (level << 15)
            if index + 1 < len(runs):
                level, duration = runs[index + 1]
                word |= (duration | (level << 15)) << 16
            words.append(word)
        words.append(0)
        captures.append(
            {
                "id": identity,
                "count": len(words),
                "checksum": f"{checksum(words):08x}",
                "arm_us": 100000 * identity,
                "callback_us": 100000 * identity + 90000,
                "poll_us": 100000 * identity + 91000,
                "seal_us": 100000 * identity + 92000,
                "gap_us": 0,
                "chunks": [
                    "".join(f"{word:08x}" for word in words[index : index + 16]) for index in range(0, len(words), 16)
                ],
            }
        )
    return {
        "session": {
            "id": 42,
            "duration_ms": 30000,
            "state": "aborted",
            "exports": 2,
            "suppressed": 3,
            "end_us": 600000,
        },
        "captures": captures,
    }
