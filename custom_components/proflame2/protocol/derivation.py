"""Derive remote profiles from validated frames without HA session handling.

Contradictory observations raise ProfileConflictError. Insufficient command
variation is an explicit result, so adapters can request more evidence without
guessing a profile. Repetition and the CLI's confirmation gates are adapter policy.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .ecc import derive_ecc_profile, extended_frame_integrity_matches
from .models import EXTENDED_W4_FIXED_MASK, ECCProfile, ExtendedFrameTemplate, FireplaceFeatures, RemoteProfile
from .packet import ProflameFrame
from .variants import PROTOCOL_VARIANT_EXTENDED_10_WORD

DEFAULT_EXTENDED_MANUAL_W7 = 0xC8


class ProfileConflictError(ValueError):
    """Frames are invalid or disagree about the remote's identity/profile."""


@dataclass(frozen=True)
class LearningRequirements:
    """Command variation requirements; adapters may preserve older HA policy."""

    min_frames: int = 3
    min_commands: int = 3
    min_cmd1: int = 2
    min_cmd2: int = 2
    min_manual_w5: int = 2


@dataclass(frozen=True)
class ProfileDerivation:
    """Either a complete profile or plain-language missing evidence."""

    profile: RemoteProfile | None
    missing: tuple[str, ...] = ()


DEFAULT_REQUIREMENTS = LearningRequirements()


def validate_frame(frame: ProflameFrame) -> None:
    """Reject invalid fields before wire_words can mask out-of-range values."""
    if type(frame.serial_id) is not int or not 0 <= frame.serial_id <= 0xFFFFFF:
        raise ProfileConflictError("Remote identity must be a 24-bit integer.")
    values = (frame.cmd1, frame.cmd2, frame.err1, frame.err2, *frame.extension_words)
    if any(type(value) is not int or not 0 <= value <= 255 for value in values):
        raise ProfileConflictError("Command and integrity fields must be byte integers.")
    if frame.is_extended and not extended_frame_integrity_matches(frame.wire_words):
        raise ProfileConflictError("An extended command failed its integrity checks.")


def derive_remote_profile(
    frames: Iterable[ProflameFrame],
    features: FireplaceFeatures,
    *,
    requirements: LearningRequirements = DEFAULT_REQUIREMENTS,
) -> ProfileDerivation:
    """Validate every observation and derive one consistent remote profile.

    Fixed-field checks apply to manual-mode extended frames. W7 is received
    telemetry and is deliberately replaced by the established manual TX value.
    """
    observations = tuple(frames)
    if not observations:
        return ProfileDerivation(None, ("No fireplace commands have been received.",))
    for frame in observations:
        validate_frame(frame)
    identities = {frame.serial_id for frame in observations}
    variants = {frame.is_extended for frame in observations}
    if len(identities) != 1 or len(variants) != 1:
        raise ProfileConflictError("Commands must come from one remote and one protocol format.")

    extended = observations[0].is_extended
    missing = []
    commands = {(frame.cmd1, frame.cmd2) for frame in observations}
    if len(observations) < requirements.min_frames or len(commands) < requirements.min_commands:
        missing.append(f"At least {requirements.min_commands} different commands are needed.")
    serial_id = observations[0].serial_id
    if not extended:
        # Validate consistency even before there is enough command variation.
        try:
            ecc = derive_ecc_profile(
                ((frame.cmd1, frame.err1) for frame in observations),
                ((frame.cmd2, frame.err2) for frame in observations),
            )
        except ValueError as exc:
            raise ProfileConflictError("Commands disagree about the remote's integrity values.") from exc
        if len({frame.cmd1 for frame in observations}) < requirements.min_cmd1:
            missing.append("More different power, light, or mode commands are needed.")
        if len({frame.cmd2 for frame in observations}) < requirements.min_cmd2:
            missing.append("More different flame, fan, or other accessory commands are needed.")
        profile = RemoteProfile(serial_id, ecc, features)
    else:
        manual = [frame for frame in observations if frame.cmd1 & 0x03 == 0x01]
        templates = {(frame.cmd1 & EXTENDED_W4_FIXED_MASK, frame.err1, frame.extension_words[0]) for frame in manual}
        if len(templates) > 1:
            raise ProfileConflictError("Manual commands have inconsistent fixed template fields.")
        if not manual or len({frame.cmd2 for frame in manual}) < requirements.min_manual_w5:
            missing.append("More different commands with the remote in manual mode are needed.")
        profile = None
        if templates:
            w4_base, w6, w8 = next(iter(templates))
            profile = RemoteProfile(
                serial_id,
                ECCProfile(0, 0, 0, 0),
                features,
                protocol_variant=PROTOCOL_VARIANT_EXTENDED_10_WORD,
                extended_template=ExtendedFrameTemplate(w4_base, w6, DEFAULT_EXTENDED_MANUAL_W7, w8),
            )
    return ProfileDerivation(None, tuple(missing)) if missing else ProfileDerivation(profile)
