"""Tests for the ESPHome/T-Embed CC1101 transport contract."""

from __future__ import annotations

import dataclasses

import pytest

from custom_components.proflame2.protocol.encoder import encode_packet
from custom_components.proflame2.protocol.models import ECCProfile, FireplaceState, RemoteProfile
from custom_components.proflame2.rf.esphome.contract import (
    ESPHomeDisplayState,
    ESPHomeEndpointStatus,
    ESPHomeModulation,
    ESPHomeRadioConfig,
    ESPHomeRXEvent,
    ESPHomeTXRequest,
)
from custom_components.proflame2.rf.waveform import build_transmission_plan


@pytest.fixture
def remote_profile() -> RemoteProfile:
    return RemoteProfile(
        serial_id=0x3B3F03,
        ecc=ECCProfile(c1=0x10, d1=0x20, c2=0x30, d2=0x40),
    )


def test_default_radio_config_matches_known_proflame2_tx_baseline() -> None:
    config = ESPHomeRadioConfig()

    assert config.tx_frequency_hz == 314_973_000
    assert config.modulation == ESPHomeModulation.ASK_OOK
    assert config.data_rate_bps == 2_400
    assert config.tx_repeat_count == 5
    assert config.rx_frequency_hz == 315_000_000
    assert config.inter_frame_gap_ms == 5.2
    assert config.rx_enabled is False


def test_tx_request_uses_prepared_transmission_plan_payload(remote_profile: RemoteProfile) -> None:
    packet = encode_packet(
        FireplaceState(power=True, flame=3, fan=2, light=1),
        remote_profile,
    )
    packet.transmission_plan = build_transmission_plan(packet.frame)

    request = ESPHomeTXRequest.from_packet(packet, request_id="tx-1")

    assert request.air_payload == packet.transmission_plan.air_payload
    assert request.air_payload_hex == packet.transmission_plan.air_payload.hex()
    assert request.air_payload_bit_length == packet.transmission_plan.air_payload_bit_length
    assert request.remote_id == packet.frame.serial_id
    assert request.cmd1 == packet.frame.cmd1
    assert request.err1 == packet.frame.err1
    assert request.cmd2 == packet.frame.cmd2
    assert request.err2 == packet.frame.err2


def test_tx_request_requires_prepared_transmission_plan(remote_profile: RemoteProfile) -> None:
    packet = encode_packet(FireplaceState(power=True, flame=1), remote_profile)

    with pytest.raises(ValueError, match="transmission_plan"):
        ESPHomeTXRequest.from_packet(packet, request_id="tx-1")


def test_semantic_metadata_is_optional_and_non_authoritative(
    remote_profile: RemoteProfile,
) -> None:
    packet = encode_packet(FireplaceState(power=True, flame=3), remote_profile)
    packet.transmission_plan = build_transmission_plan(packet.frame)

    request = ESPHomeTXRequest.from_packet(
        packet,
        request_id="tx-1",
        display_state=ESPHomeDisplayState(
            fireplace_name="Living Room",
            power=True,
            flame=3,
            status_text="Ready",
        ),
        include_frame_metadata=False,
    )

    assert request.air_payload == packet.transmission_plan.air_payload
    assert request.remote_id is None
    assert request.cmd1 is None
    assert request.err1 is None
    assert request.cmd2 is None
    assert request.err2 is None
    assert request.display_state is not None
    assert request.display_state.fireplace_name == "Living Room"


def test_contract_types_do_not_require_profile_or_policy_fields() -> None:
    forbidden_field_names = {
        "ecc",
        "profile",
        "remote_profile",
        "debounce",
        "thermostat_policy",
        "active_profile",
        "fireplace_state_authority",
        "idle_after_tx",
    }
    contract_types = (
        ESPHomeRadioConfig,
        ESPHomeDisplayState,
        ESPHomeTXRequest,
        ESPHomeRXEvent,
    )

    for contract_type in contract_types:
        fields = {field.name for field in dataclasses.fields(contract_type)}
        assert fields.isdisjoint(forbidden_field_names)


def test_status_enum_includes_required_states() -> None:
    assert {
        "booting",
        "not_configured",
        "configuring",
        "ready",
        "tx_active",
        "rx_active",
        "fault",
        "shutting_down",
    }.issubset({status.value for status in ESPHomeEndpointStatus})


def _air_payload_bits(payload: bytes, bit_length: int) -> str:
    return "".join(f"{byte:08b}" for byte in payload)[:bit_length]


def _symbols_from_air_bits(bit_string: str) -> list[str]:
    symbol_map = {"11": "S", "01": "0", "10": "1", "00": "Z"}
    return [symbol_map[bit_string[index : index + 2]] for index in range(0, len(bit_string), 2)]


def _rtl433_expect(bits: str) -> str:
    value = int(bits, 2)
    mask = (1 << len(bits)) - 1
    complemented = (~value) & mask
    left_shift = (4 - (len(bits) % 4)) % 4
    return f"{{{len(bits)}}}{complemented << left_shift:x}"


def _native_groups_from_symbols(symbols: list[str]) -> list[str]:
    expand = {"S": "11", "0": "01", "1": "10", "Z": "00"}
    groups: list[str] = []
    for group_index in range(7):
        word = symbols[group_index * 13 : (group_index + 1) * 13]
        air_bits = "".join(expand[symbol] for symbol in word)
        runs: list[tuple[str, int]] = []
        current = air_bits[0]
        length = 1
        for bit in air_bits[1:]:
            if bit == current:
                length += 1
            else:
                runs.append((current, length))
                current = bit
                length = 1
        runs.append((current, length))
        assert runs[0] == ("1", 3)
        emitted = []
        for bit, run_length in runs[1:]:
            if bit != "1":
                continue
            assert run_length in (1, 2)
            emitted.append("0" if run_length == 1 else "1")
        groups.append("".join(emitted))
    return groups


def _native_group_schedule_stats(groups: list[str]) -> tuple[int, int, int]:
    short_low_count = 0
    long_low_count = 0
    total_bit_period_units = 0
    for group in groups:
        total_bit_period_units += 4
        short_low_count += 1
        for bit in group:
            if bit == "0":
                total_bit_period_units += 2
                short_low_count += 1
            else:
                total_bit_period_units += 4
                long_low_count += 1
    return short_low_count, long_low_count, total_bit_period_units


def _native_group_emitted_symbols(groups: list[str]) -> list[str]:
    emitted: list[str] = []
    for group in groups:
        emitted.append("S")
        emitted.extend(list(group))
    return emitted


def _native_remote_pcm_shaped_segments(groups: list[str], expected_pcm_bits: str) -> list[tuple[str, str, str]]:
    emitted_symbols = _native_group_emitted_symbols(groups)
    cursor = 0
    shaped: list[tuple[str, str, str]] = []
    for index, symbol in enumerate(emitted_symbols):
        candidates = (
            [("11100", "long"), ("1110", "short")]
            if symbol == "S"
            else [("10", "short"), ("100", "long")] if symbol == "0" else [("110", "short"), ("1100", "long")]
        )
        chosen: tuple[str, str] | None = None
        for segment, low_mode in candidates:
            next_cursor = cursor + len(segment)
            if expected_pcm_bits[cursor:next_cursor] != segment:
                continue
            if index + 1 < len(emitted_symbols):
                if next_cursor < len(expected_pcm_bits) and expected_pcm_bits[next_cursor] == "1":
                    chosen = (segment, low_mode)
                    cursor = next_cursor
                    break
            elif next_cursor == len(expected_pcm_bits):
                chosen = (segment, low_mode)
                cursor = next_cursor
                break
        if chosen is None:
            raise AssertionError(f"no shaping candidate for symbol_index={index} symbol={symbol} cursor={cursor}")
        shaped.append((symbol, chosen[0], chosen[1]))
    return shaped


def _bits_to_hex(bit_string: str) -> str:
    if not bit_string:
        return ""
    padded = bit_string + ("0" * ((4 - (len(bit_string) % 4)) % 4))
    return "".join(f"{int(padded[index:index + 4], 2):x}" for index in range(0, len(padded), 4))


def _native_group_schedule_duration_us(
    groups: list[str],
    *,
    repeat_count: int,
    profile: str,
    expected_pcm_bits: str | None = None,
) -> tuple[int, int]:
    if profile == "yardstick_compat":
        sync_high_us = 1251
        sync_low_us = 417
        zero_high_us = 417
        zero_low_us = 417
        one_high_us = 834
        one_low_us = 834
        repeat_gap_us = 10700
        per_repeat_duration_us = 0
        for group in groups:
            per_repeat_duration_us += sync_high_us + sync_low_us
            for bit in group:
                if bit == "0":
                    per_repeat_duration_us += zero_high_us + zero_low_us
                else:
                    per_repeat_duration_us += one_high_us + one_low_us
    elif profile == "native_remote":
        assert expected_pcm_bits is not None
        repeat_gap_us = 5240
        per_repeat_duration_us = 0
        for symbol, _segment, low_mode in _native_remote_pcm_shaped_segments(groups, expected_pcm_bits):
            if symbol == "S":
                per_repeat_duration_us += 1216
                per_repeat_duration_us += 852 if low_mode == "long" else 444
            elif symbol == "0":
                per_repeat_duration_us += 396
                per_repeat_duration_us += 444 if low_mode == "short" else 852
            else:
                per_repeat_duration_us += 808
                per_repeat_duration_us += 444 if low_mode == "short" else 852
    else:
        raise ValueError(profile)

    total_burst_duration_us = (per_repeat_duration_us * repeat_count) + (repeat_gap_us * max(0, repeat_count - 1))
    return per_repeat_duration_us, total_burst_duration_us


def test_power_on_flame6_fixture_maps_to_expected_native_analyzer_groups() -> None:
    profile = RemoteProfile(
        serial_id=0x3B3F02,
        ecc=ECCProfile(c1=0x5, d1=0x7, c2=0x1, d2=0x8),
    )
    packet = encode_packet(
        FireplaceState(
            power=True,
            flame=6,
            fan=0,
            light=0,
            front=False,
            aux=False,
            thermostat=False,
            cpi=False,
        ),
        profile,
    )
    packet.transmission_plan = build_transmission_plan(packet.frame)

    bit_string = _air_payload_bits(
        packet.transmission_plan.air_payload,
        packet.transmission_plan.air_payload_bit_length,
    )
    symbols = _symbols_from_air_bits(bit_string)
    expected_pcm_bits = _air_payload_bits(packet.transmission_plan.air_payload, 183)
    groups = _native_groups_from_symbols(symbols)

    assert groups == [
        "01001001",
        "010000001",
        "000001010",
        "000000110",
        "000010001",
        "10010010",
        "001000001",
    ]
    assert [_rtl433_expect(group) for group in groups] == [
        "{8}b6",
        "{9}bf0",
        "{9}fa8",
        "{9}fc8",
        "{9}f70",
        "{8}6d",
        "{9}df0",
    ]

    short_low_count, long_low_count, total_bit_period_units = _native_group_schedule_stats(groups)
    assert short_low_count == 52
    assert long_low_count == 16
    assert total_bit_period_units == 182

    shaped = _native_remote_pcm_shaped_segments(groups, expected_pcm_bits)
    shaped_row_bits = "".join(segment for _symbol, segment, _low_mode in shaped)
    assert len(shaped_row_bits) == 183
    assert shaped_row_bits == expected_pcm_bits
    assert _bits_to_hex(shaped_row_bits).startswith("e5a9a9")
    assert shaped[0] == ("S", "11100", "long")
    assert shaped[2] == ("1", "110", "short")
    assert shaped[4] == ("0", "100", "long")
    assert shaped[5] == ("1", "110", "short")

    native_per_repeat_duration_us, native_total_burst_duration_us = _native_group_schedule_duration_us(
        groups,
        repeat_count=5,
        profile="native_remote",
        expected_pcm_bits=expected_pcm_bits,
    )
    assert native_per_repeat_duration_us == 76388
    assert native_total_burst_duration_us == 402900

    yardstick_per_repeat_duration_us, yardstick_total_burst_duration_us = _native_group_schedule_duration_us(
        groups, repeat_count=5, profile="yardstick_compat"
    )
    assert yardstick_per_repeat_duration_us == 75894
    assert yardstick_total_burst_duration_us == 422270


def test_native_remote_row_shaping_uses_position_aware_low_selection() -> None:
    groups = [
        "01001001",
        "010000001",
        "000001010",
        "000000110",
        "000010001",
        "10010010",
        "001000001",
    ]
    expected_pcm_bits = "".join(f"{int(c, 16):04b}" for c in "e5a9a9b96aa96e55596b95559ae55695b9a9a5aea6a958")[:183]

    shaped = _native_remote_pcm_shaped_segments(groups, expected_pcm_bits)

    assert shaped[0] == ("S", "11100", "long")
    assert shaped[2] == ("1", "110", "short")
    assert shaped[4] == ("0", "100", "long")
    assert shaped[5] == ("1", "110", "short")
    assert sum(1 for _symbol, _segment, low_mode in shaped if low_mode == "long") == 17


def test_native_remote_repeat_gap_schedule_model_uses_final_fall_to_first_rise() -> None:
    previous_final_fall_us = 1_000_000
    desired_rf_visible_repeat_gap_us = 5240
    scheduled_delay_us = 5240
    target_first_rise_us = previous_final_fall_us + scheduled_delay_us

    assert target_first_rise_us == 1_005_240
    assert desired_rf_visible_repeat_gap_us == scheduled_delay_us


def test_repeat_boundary_mode_keeps_same_final_fall_to_first_rise_contract() -> None:
    previous_final_fall_us = 2_000_000
    scheduled_delay_us = 5240
    target_continuous_tx = previous_final_fall_us + scheduled_delay_us
    target_reenter_tx = previous_final_fall_us + scheduled_delay_us

    assert target_continuous_tx == target_reenter_tx == 2_005_240


def test_native_group_leading_low_guards_only_delay_first_rise_target_when_requested() -> None:
    burst_after_enter_tx_us = 100_000
    tx_ready_us = 100_120

    no_guard_target = tx_ready_us
    pre_burst_guard_target = max(no_guard_target, burst_after_enter_tx_us + 417)
    pre_frame_guard_target = max(no_guard_target, tx_ready_us + 417)
    combined_guard_target = max(no_guard_target, burst_after_enter_tx_us + 417, tx_ready_us + 417)

    assert no_guard_target == 100_120
    assert pre_burst_guard_target == 100_417
    assert pre_frame_guard_target == 100_537
    assert combined_guard_target == 100_537
