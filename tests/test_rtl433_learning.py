"""Complete extended rtl_433 frames survive HA adapters and Learning ID import."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

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
from custom_components.proflame2.rtl433_learning import normalize_learning_id_profile_input
from custom_components.proflame2.runtime import async_get_runtime_entries

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


def captured_extended_profiles():
    """Use recorded command sequences, never synthetic repetitions, for import."""
    corpus = json.loads((Path(__file__).parent / "fixtures/rtl433_helper_decoder_output.json").read_text())
    for remote in corpus["remotes"]:
        if remote["records"][0]["protocol_variant"] != PROTOCOL_VARIANT_EXTENDED_10_WORD:
            continue
        fixed = 0x84 if "mendota" in remote["name"] else 0x80
        observations = [row for row in remote["records"] if row["cmd1"] & 0x8C == fixed and row["thermostat"] == 0]
        first = observations[0]
        profile = RemoteProfile(
            first["id"],
            ECCProfile(0, 0, 0, 0),
            FEATURES,
            protocol_variant=PROTOCOL_VARIANT_EXTENDED_10_WORD,
            extended_template=ExtendedFrameTemplate(fixed, first["w6"], 0xC8, first["w8"]),
        )
        yield pytest.param(profile, observations, id=remote["name"])


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


def test_mixed_extended_and_legacy_paste_rejected():
    samples = parse_rtl433_samples("\n".join(map(json.dumps, records(EXTENDED, 3) + records(LEGACY, 3))))
    with pytest.raises(Rtl433ProfileDerivationError):
        normalize_manual_rtl433_profile_samples({CONF_NAME: "Fireplace", CONF_BACKEND_TYPE: BACKEND_ESPHOME}, samples)


@pytest.mark.parametrize(
    ("profile", "source_records"),
    [
        pytest.param(LEGACY, records(LEGACY), id="synthetic-legacy"),
        pytest.param(EXTENDED, records(EXTENDED), id="synthetic-extended"),
        *captured_extended_profiles(),
    ],
)
async def test_installed_helper_id_imports_into_ha_and_preserves_manual_encoding(
    profile, source_records, hass, monkeypatch, enable_custom_integrations
):
    python = os.environ.get("PROFLAME2_HELPER_PYTHON")
    if not python:
        pytest.skip("Run validate_package.py --ha-interoperability for installed-helper validation")
    process = subprocess.run(
        [python, "-I", "-m", "proflame2_learning_id.cli", "--input-mode", "file"],
        input="\n".join(map(json.dumps, source_records)),
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
    # Exercise the UI boundary, rather than treating an adapter call as a
    # substitute for importing a helper-generated ID through Home Assistant.
    flow = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert flow["menu_options"] == ["learn", "manual", "manual_legacy"]
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], {"next_step_id": "manual"})
    flow = await hass.config_entries.flow.async_configure(
        flow["flow_id"],
        {CONF_NAME: "Helper Import", CONF_BACKEND_TYPE: BACKEND_FAKE, CONF_LEARNING_ID: process.stdout.strip()},
    )
    assert flow["step_id"] == "manual_features"
    flow = await hass.config_entries.flow.async_configure(flow["flow_id"], asdict(profile.features))
    assert flow["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_LEARNING_ID not in flow["data"]
    await hass.async_block_till_done()
    imported_entry = flow["result"]
    assert imported_entry.data[CONF_REMOTE_ID] == profile.serial_id
    runtime = async_get_runtime_entries(hass)[imported_entry.entry_id]
    assert runtime.remote_profile == profile
    assert runtime.learning_id == process.stdout.strip()
    assert runtime.refresh_schedule.period_seconds == 0
    for flame in range(1, 7):
        for light in range(8):
            for fan in range(7):
                state = FireplaceState(True, flame=flame, light=light, fan=fan)
                assert encode_state(state, runtime.remote_profile).wire_words == encode_state(state, profile).wire_words
    assert await hass.config_entries.async_reload(imported_entry.entry_id)
    await hass.async_block_till_done()
    assert async_get_runtime_entries(hass)[imported_entry.entry_id].remote_profile == profile
    assert await hass.config_entries.async_unload(imported_entry.entry_id)
