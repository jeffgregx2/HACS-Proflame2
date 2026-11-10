"""Deterministic extended manual profiles, independent of observed temperatures."""

from dataclasses import replace

from .models import ExtendedFrameTemplate, RemoteProfile

EXTENDED_IDENTITY_FLAGS_MASK = 0x88
DEFAULT_EXTENDED_MANUAL_W6 = 20
DEFAULT_EXTENDED_MANUAL_W7 = 0xC8
DEFAULT_EXTENDED_MANUAL_W8 = 0


def canonical_manual_template(w4_base: int) -> ExtendedFrameTemplate:
    """Retain identity flags and use Celsius with fixed 20-degree values."""
    return ExtendedFrameTemplate(
        w4_base & EXTENDED_IDENTITY_FLAGS_MASK,
        DEFAULT_EXTENDED_MANUAL_W6,
        DEFAULT_EXTENDED_MANUAL_W7,
        DEFAULT_EXTENDED_MANUAL_W8,
    )


def canonical_manual_profile(profile: RemoteProfile) -> RemoteProfile:
    """Fix unused temperature fields for manual TX and Learning ID exchange.

    Apply to profiles only, never to received frames before integrity checks.
    Celsius is required for the fixed 20-degree setpoint. Preserve the remote
    identity, retained W4 flags, and local feature configuration.
    """
    if not profile.is_extended:
        return profile
    template = profile.extended_template
    assert template is not None
    for value in (template.w4_base, template.w6, template.w7, template.w8):
        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError("Learning ID profile field is out of range.")
    return replace(profile, extended_template=canonical_manual_template(template.w4_base))
