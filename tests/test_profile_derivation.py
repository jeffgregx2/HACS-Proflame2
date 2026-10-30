"""Shared profile policy must reject contradictory evidence before extraction."""

from dataclasses import replace

import pytest

from custom_components.proflame2.protocol.derivation import (
    LearningRequirements,
    ProfileConflictError,
    derive_remote_profile,
)
from custom_components.proflame2.protocol.encoder import encode_state
from custom_components.proflame2.protocol.models import (
    ECCProfile,
    ExtendedFrameTemplate,
    FireplaceFeatures,
    FireplaceState,
    RemoteProfile,
)
from custom_components.proflame2.protocol.variants import PROTOCOL_VARIANT_EXTENDED_10_WORD

FEATURES = FireplaceFeatures()
LEGACY = RemoteProfile(0x123456, ECCProfile(5, 7, 1, 8), FEATURES)
EXTENDED = RemoteProfile(
    0x123456,
    ECCProfile(0, 0, 0, 0),
    FEATURES,
    protocol_variant=PROTOCOL_VARIANT_EXTENDED_10_WORD,
    extended_template=ExtendedFrameTemplate(0x80, 0x15, 0xC8, 0),
)


def frames(profile):
    return [
        encode_state(FireplaceState(True, flame=flame, light=light), profile)
        for flame, light in ((1, 0), (2, 0), (2, 1))
    ]


@pytest.mark.parametrize("profile", [LEGACY, EXTENDED])
def test_profile_derivation_matches_encoder(profile):
    assert derive_remote_profile(frames(profile), FEATURES).profile == profile


@pytest.mark.parametrize("profile", [LEGACY, EXTENDED])
def test_repeated_command_cannot_replace_variation(profile):
    result = derive_remote_profile([frames(profile)[0]] * 20, FEATURES)
    assert result.profile is None
    assert result.missing


@pytest.mark.parametrize("profile", [LEGACY, EXTENDED])
def test_conflicts_not_hidden_by_insufficient_counts(profile):
    first = frames(profile)[0]
    with pytest.raises(ProfileConflictError):
        derive_remote_profile([first, replace(first, serial_id=0x654321)], FEATURES)


def test_invalid_extended_integrity_rejected():
    first = frames(EXTENDED)[0]
    with pytest.raises(ProfileConflictError):
        derive_remote_profile([replace(first, err2=first.err2 ^ 1)], FEATURES)


def test_extended_template_conflict_with_valid_integrity_rejected():
    other = replace(EXTENDED, extended_template=ExtendedFrameTemplate(0x80, 0x16, 0xC8, 0))
    with pytest.raises(ProfileConflictError):
        derive_remote_profile([*frames(EXTENDED), *frames(other)], FEATURES)


def test_mixed_variants_rejected():
    with pytest.raises(ProfileConflictError):
        derive_remote_profile([*frames(LEGACY), *frames(EXTENDED)], FEATURES)


def test_legacy_command_integrity_conflict_rejected():
    first = frames(LEGACY)[0]
    with pytest.raises(ProfileConflictError):
        derive_remote_profile([first, replace(first, err1=first.err1 ^ 1)], FEATURES)


def test_legacy_paste_policy_can_remain_single_frame():
    policy = LearningRequirements(min_frames=1, min_commands=1, min_cmd1=1, min_cmd2=1)
    assert derive_remote_profile(frames(LEGACY)[:1], FEATURES, requirements=policy).profile == LEGACY


@pytest.mark.parametrize("value", [-1, 256, True, 1.5])
def test_out_of_range_fields_not_masked(value):
    with pytest.raises(ProfileConflictError):
        derive_remote_profile([replace(frames(LEGACY)[0], cmd1=value)], FEATURES)
