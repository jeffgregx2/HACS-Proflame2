"""Helpers for Proflame 2 Err-byte calculation and C/D derivation.

The Proflame 2 protocol uses an ``Err`` byte alongside each command byte.
Reverse-engineering work from the SmartFire project showed that this value is
not a generic CRC across the full packet. Instead, each command group is
paired with a stable 8-bit ``CD`` constant, where the high nibble is ``C`` and
the low nibble is ``D``.

This module intentionally keeps two separate responsibilities together:

1. Forward calculation:
   Given a command byte and known C/D nibbles, compute the Err byte exactly as
   the original remote does.
2. Reverse derivation:
   Given observed ``command -> Err`` pairs from captured traffic, recover the
   stable C/D value for that command group.

At the moment the reverse step is implemented as a brute-force search over the
full 0x00..0xFF CD space. That is deliberate. The search space is tiny, the
behavior is deterministic, and brute force keeps the code easy to audit
against captures while the protocol work is still being validated.

The formulas here are based on the SmartFire reverse-engineering reference:

- ``https://github.com/JoelB/smartfire/blob/main/calculateCandD/calc.cs``
  for candidate derivation
- ``https://github.com/JoelB/smartfire/blob/main/smartfire_controller/fireplace.py``
  for forward Err-byte generation

Credit:
This module intentionally re-implements SmartFire's reverse-engineered Proflame2
ECC behavior in Python for this project. The original reverse-engineering and
reference code live in the SmartFire repository:

- ``https://github.com/JoelB/smartfire``
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import ECCProfile


def build_err_byte(command: int, c_value: int, d_value: int) -> int:
    """Build the Proflame 2 Err byte from a command and 4-bit C/D constants.

    This function explicitly mirrors SmartFire's implementation from:

    - ``https://github.com/JoelB/smartfire/blob/main/calculateCandD/calc.cs``
    - ``https://github.com/JoelB/smartfire/blob/main/smartfire_controller/fireplace.py``

    Credit for the original reverse-engineered algorithm belongs to the
    SmartFire project:

    - ``https://github.com/JoelB/smartfire``

    The command byte is split into its high and low nibbles. The high Err
    nibble is derived from ``C`` plus a XOR mix of the command nibbles and
    one-bit-left-shifted versions of those nibbles. The low Err nibble is
    derived from ``D`` plus a simpler XOR of the original command nibbles.

    We mask everything back to 4 bits to match the nibble arithmetic performed
    in the original implementation and on the wire.
    """

    command &= 0xFF
    c_value &= 0x0F
    d_value &= 0x0F
    high_nibble = (command >> 4) & 0x0F
    low_nibble = command & 0x0F
    err_high = (c_value ^ high_nibble ^ ((high_nibble << 1) & 0x0F) ^ ((low_nibble << 1) & 0x0F)) & 0x0F
    err_low = (d_value ^ high_nibble ^ low_nibble) & 0x0F
    return (err_high << 4) | err_low


def combine_cd(c_value: int, d_value: int) -> int:
    """Combine 4-bit C and D values into the SmartFire-style CD byte.

    SmartFire models the learned constant as one byte because that makes brute-
    force candidate enumeration simpler. We keep the helpers both ways so the
    public profile can stay explicit as ``c1/d1`` and ``c2/d2``.
    """

    return ((c_value & 0x0F) << 4) | (d_value & 0x0F)


def split_cd(cd_value: int) -> tuple[int, int]:
    """Split the SmartFire-style CD byte into 4-bit C and D values."""

    return ((cd_value >> 4) & 0x0F, cd_value & 0x0F)


def derive_cd_candidates(command: int, observed_err: int) -> tuple[int, ...]:
    """Return every CD byte that matches an observed command/Err pair.

    This is intentionally brute-force. For a single observed pair we simply try
    all 256 possible CD values and keep the ones whose forward calculation
    reproduces the observed Err byte.

    The brute-force candidate search is a direct adaptation of the SmartFire
    reverse-engineering approach documented in:

    - ``https://github.com/JoelB/smartfire/blob/main/calculateCandD/calc.cs``
    - repository: ``https://github.com/JoelB/smartfire``

    Returning all matches instead of only one candidate is important for two
    reasons:

    1. It makes ambiguity explicit instead of hiding it.
    2. It lets later code intersect candidate sets from multiple captures and
       prove that one stable C/D value explains the full command group.
    """

    return tuple(
        cd_value for cd_value in range(0x100) if build_err_byte(command, *split_cd(cd_value)) == (observed_err & 0xFF)
    )


def derive_unique_cd(command: int, observed_err: int) -> int:
    """Return the unique CD byte for a command/Err pair or raise.

    This helper is useful when one capture already collapses to a single
    candidate, but callers should not assume that will always be true for every
    remote or protocol variant. The safer learning path is still to collect
    multiple captures and call :func:`derive_stable_cd`.
    """

    candidates = derive_cd_candidates(command, observed_err)
    if not candidates:
        raise ValueError("No matching C/D candidate found for the observed Err byte.")
    if len(candidates) > 1:
        raise ValueError(f"Ambiguous C/D derivation for command 0x{command:02X}: {len(candidates)} candidates.")
    return candidates[0]


def derive_stable_cd(samples: Iterable[tuple[int, int]]) -> int:
    """Find the unique stable CD byte shared by a set of command/Err samples.

    Proflame 2 uses one stable C/D value for the whole Cmd1 group and one
    stable C/D value for the whole Cmd2 group. We validate that assumption by
    deriving brute-force candidates for each observed sample and intersecting
    those candidate sets.

    If the intersection is empty, the capture set is contradictory or the
    decoder is wrong. If the intersection contains more than one value, we have
    not yet collected enough distinguishing captures.
    """

    candidate_sets = [set(derive_cd_candidates(command, observed_err)) for command, observed_err in samples]
    if not candidate_sets:
        raise ValueError("At least one command/Err sample is required.")

    stable_candidates = set.intersection(*candidate_sets)
    if not stable_candidates:
        raise ValueError("No stable C/D value matches all provided samples.")
    if len(stable_candidates) > 1:
        raise ValueError(f"Ambiguous stable C/D derivation: {len(stable_candidates)} candidates remain.")
    return next(iter(stable_candidates))


def derive_ecc_profile(cmd1_samples: Iterable[tuple[int, int]], cmd2_samples: Iterable[tuple[int, int]]) -> ECCProfile:
    """Derive the stable ECC profile from Cmd1 and Cmd2 capture sets.

    This is the bridge between raw capture analysis and the protocol model used
    by the encoder/decoder. Once a remote's stable Cmd1 and Cmd2 C/D values are
    known, we can construct arbitrary valid packets for that remote profile
    without needing the original handheld remote again.
    """

    c1, d1 = split_cd(derive_stable_cd(cmd1_samples))
    c2, d2 = split_cd(derive_stable_cd(cmd2_samples))
    return ECCProfile(c1=c1, d1=d1, c2=c2, d2=d2)


def err1_for(command: int, profile: ECCProfile) -> int:
    """Return the Err byte for a Cmd1 command using the learned profile."""

    return build_err_byte(command, profile.c1, profile.d1)


def err2_for(command: int, profile: ECCProfile) -> int:
    """Return the Err byte for a Cmd2 command using the learned profile."""

    return build_err_byte(command, profile.c2, profile.d2)
