"""Shared profile policy must reject contradictory evidence before extraction."""

from dataclasses import replace
from itertools import permutations

import pytest

from custom_components.proflame2.protocol.derivation import (
    LearningRequirements,
    ProfileConflictError,
    derive_remote_profile,
)
from custom_components.proflame2.protocol.encoder import encode_state
from custom_components.proflame2.protocol.learning_id import encode_learning_id
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
    extended_template=ExtendedFrameTemplate(0x80, 20, 0xC8, 0),
)


def frames(profile):
    cpi = bool(profile.extended_template and profile.extended_template.w4_base & 0x80)
    return [
        encode_state(FireplaceState(True, flame=flame, light=light, cpi=cpi), profile)
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
    other = replace(EXTENDED, extended_template=ExtendedFrameTemplate(0x88, 20, 0xC8, 0))
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


def test_extended_temperature_settings_do_not_change_identity():
    """Validate original C/F evidence, then derive one canonical manual profile."""
    observations = []
    for index, (flags, setpoint, room) in enumerate(((0x84, 79, 767), (0x80, 26, 245), (0x84, 78, 768))):
        profile = replace(EXTENDED, extended_template=ExtendedFrameTemplate(flags, setpoint, room & 255, room >> 8))
        observations.append(encode_state(FireplaceState(True, flame=index + 1, cpi=True), profile))
    expected = replace(EXTENDED, extended_template=ExtendedFrameTemplate(0x80, 20, 0xC8, 0))
    original = tuple(frame.wire_words for frame in observations)
    for ordered in permutations(observations):
        learned = derive_remote_profile(ordered, FEATURES).profile
        assert learned == expected
        assert encode_learning_id(learned) == encode_learning_id(expected)
    assert tuple(frame.wire_words for frame in observations) == original


def test_units_switches_cannot_supply_command_diversity():
    observations = []
    for flags, setpoint in ((0x80, 26), (0x84, 79), (0x80, 25)):
        profile = replace(EXTENDED, extended_template=ExtendedFrameTemplate(flags, setpoint, 200, 0))
        observations.append(encode_state(FireplaceState(True, flame=2, cpi=True), profile))
    assert derive_remote_profile(observations, FEATURES).profile is None


@pytest.mark.parametrize("field", ["cmd1", "err1", "err2", "extension_words"])
def test_original_units_and_temperature_integrity_cannot_be_hidden(field):
    first = frames(EXTENDED)[0]
    change = {field: (1, *first.extension_words[1:]) if field == "extension_words" else getattr(first, field) ^ 4}
    with pytest.raises(ProfileConflictError, match="integrity"):
        derive_remote_profile([replace(first, **change), *frames(EXTENDED)], FEATURES)
