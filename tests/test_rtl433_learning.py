"""Complete extended rtl_433 frames survive HA adapters and Learning ID import."""

from __future__ import annotations

import json
import os
import subprocess

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.proflame2.config_flow import Proflame2ConfigFlow
from custom_components.proflame2.const import (
    BACKEND_ESPHOME,
    BACKEND_FAKE,
    CONF_BACKEND_TYPE,
    CONF_EXTENDED_W4_BASE,
    CONF_LEARNING_ID,
    CONF_NAME,
    CONF_PROTOCOL_VARIANT,
    CONF_REFRESH_PERIOD_MINUTES,
    CONF_REMOTE_ID,
    DOMAIN,
)
from custom_components.proflame2.profile import (
    Rtl433ProfileDerivationError,
    normalize_learning_id_profile_input,
    normalize_manual_rtl433_profile_samples,
    parse_rtl433_samples,
)
from custom_components.proflame2.protocol.derivation import derive_remote_profile
from custom_components.proflame2.protocol.encoder import encode_state
from custom_components.proflame2.protocol.models import (
    ECCProfile,
    ExtendedFrameTemplate,
    FireplaceFeatures,
    FireplaceState,
    RemoteProfile,
)
from custom_components.proflame2.protocol.variants import PROTOCOL_VARIANT_EXTENDED_10_WORD
from custom_components.proflame2.runtime import async_setup_runtime_entry, async_unload_runtime_entry

FEATURES = FireplaceFeatures(fan=True, light=True)
LEGACY = RemoteProfile(0x123456, ECCProfile(5, 7, 1, 8), FEATURES)
EXTENDED = RemoteProfile(
    0x123456,
    ECCProfile(0, 0, 0, 0),
    FEATURES,
    protocol_variant=PROTOCOL_VARIANT_EXTENDED_10_WORD,
    extended_template=ExtendedFrameTemplate(0x80, 0x15, 0xC8, 0),
)


def records(profile, count=7):
    result = []
    for index in range(count):
        frame = encode_state(FireplaceState(True, flame=1 + index % 6, light=index % 7), profile)
        row = dict(
            model="Proflame2-Remote", protocol_variant=profile.protocol_variant, raw_words=bytes(frame.wire_words).hex()
        )
        result.extend([row, row.copy()])
    return result


def test_extended_paste_preserves_frame_and_shared_profile():
    samples = parse_rtl433_samples("\n".join(map(json.dumps, records(EXTENDED, 3)[::2])))
    assert all(sample.frame.is_extended for sample in samples)
    result = derive_remote_profile((sample.frame for sample in samples), FEATURES)
    assert result.profile == EXTENDED
    normalized = normalize_manual_rtl433_profile_samples(
        {CONF_NAME: "Fireplace", CONF_BACKEND_TYPE: BACKEND_ESPHOME}, samples
    )
    assert normalized.profile.data[CONF_PROTOCOL_VARIANT] == EXTENDED.protocol_variant
    assert normalized.profile.data[CONF_EXTENDED_W4_BASE] == 0x80


def test_extended_config_flow_accumulates_then_completes():
    flow = Proflame2ConfigFlow()
    flow._manual_rtl433_input = {CONF_NAME: "Fireplace", CONF_BACKEND_TYPE: BACKEND_ESPHOME}
    flow._manual_rtl433_samples = []
    for index, row in enumerate(records(EXTENDED, 3)[::2]):
        flow._manual_rtl433_samples.extend(parse_rtl433_samples(json.dumps(row)))
        flow._validate_manual_rtl433_samples(flow._manual_rtl433_samples)
        result = flow._try_build_manual_rtl433_learn_result()
        if index < 2:
            assert result is None
    assert result.success
    assert result.data[CONF_PROTOCOL_VARIANT] == EXTENDED.protocol_variant
    assert result.final_packet.frame.is_extended


def test_mixed_extended_and_legacy_paste_rejected():
    samples = parse_rtl433_samples("\n".join(map(json.dumps, records(EXTENDED, 3) + records(LEGACY, 3))))
    with pytest.raises(Rtl433ProfileDerivationError):
        normalize_manual_rtl433_profile_samples({CONF_NAME: "Fireplace", CONF_BACKEND_TYPE: BACKEND_ESPHOME}, samples)


@pytest.mark.parametrize("profile", [LEGACY, EXTENDED])
async def test_installed_helper_id_imports_into_ha_and_preserves_manual_encoding(profile, hass, monkeypatch):
    python = os.environ.get("PROFLAME2_HELPER_PYTHON")
    if not python:
        pytest.skip("Run validate_package.py --ha-interoperability for installed-helper validation")
    process = subprocess.run(
        [python, "-I", "-m", "proflame2_learning_id.cli", "--features", "fan,light", "--input-mode", "file"],
        input="\n".join(map(json.dumps, records(profile))),
        capture_output=True,
        text=True,
    )
    assert process.returncode == 0, process.stderr
    monkeypatch.setenv("PROFLAME2_ENABLE_FAKE_BACKEND", "1")
    monkeypatch.setenv("PROFLAME2_BUILD", "dev")
    normalized = normalize_learning_id_profile_input(
        {CONF_NAME: "Fireplace", CONF_BACKEND_TYPE: BACKEND_FAKE, CONF_LEARNING_ID: process.stdout.strip()}
    )
    assert normalized.data[CONF_REMOTE_ID] == profile.serial_id
    assert normalized.options[CONF_REFRESH_PERIOD_MINUTES] == 0
    entry = MockConfigEntry(domain=DOMAIN, title="Fireplace", data=normalized.data, options=normalized.options)
    entry.add_to_hass(hass)
    runtime = await async_setup_runtime_entry(hass, entry)
    try:
        imported = runtime.remote_profile
        assert imported == profile
        assert runtime.refresh_schedule.period_seconds == 0
        for flame in range(1, 7):
            for light in range(8):
                for fan in range(7):
                    state = FireplaceState(True, flame=flame, light=light, fan=fan)
                    assert encode_state(state, imported).wire_words == encode_state(state, profile).wire_words
    finally:
        await async_unload_runtime_entry(hass, entry)
