"""Manual flame-zero policy and compact command evidence from Issue #33.

Command/Err pairs are literal recorded evidence. Full frames below deliberately
use a synthetic serial, never the reporter's remote identity or raw PCM.
"""

import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from custom_components.proflame2.control import StateValidationError, build_requested_state, build_staged_state
from custom_components.proflame2.protocol.decoder import decode_state
from custom_components.proflame2.protocol.encoder import encode_state
from custom_components.proflame2.protocol.models import (
    ECCProfile,
    ExtendedFrameTemplate,
    FireplaceFeatures,
    FireplaceState,
    RemoteProfile,
)
from custom_components.proflame2.protocol.packet import ProflameFrame
from custom_components.proflame2.rf.waveform import frame_to_air_bytes

# Cmd1, Err1, Cmd2, Err2, flame, fan, light: original command bytes, not generated expectations.
RECORDED_COMMANDS = (
    (0xE1, 0xDD, 0x39, 0x10, 1, 3, 6),
    (0xE1, 0xDD, 0x38, 0x31, 0, 3, 6),
    (0xA1, 0x19, 0x38, 0x31, 0, 3, 2),
    (0xE1, 0xDD, 0x68, 0xC4, 0, 6, 6),
    (0xA1, 0x19, 0x68, 0xC4, 0, 6, 2),
)
FEATURES = FireplaceFeatures(fan=True, light=True, front=True, aux=True, cpi=True)
SYNTHETIC_PROFILE = RemoteProfile(0x123456, ECCProfile(13, 2, 6, 10), FEATURES)


@pytest.mark.parametrize("vector", RECORDED_COMMANDS)
def test_recorded_commands_decode_and_reencode_without_changing_accessories(vector):
    cmd1, err1, cmd2, err2, flame, fan, light = vector
    frame = ProflameFrame(SYNTHETIC_PROFILE.serial_id, cmd1, err1, cmd2, err2)
    state = decode_state(frame, SYNTHETIC_PROFILE)
    assert state == FireplaceState(True, flame=flame, fan=fan, light=light, aux=True, cpi=True)
    assert encode_state(state, SYNTHETIC_PROFILE, allow_flame_off_when_on=True) == frame
    with pytest.raises(ValueError, match="validation byte"):
        decode_state(replace(frame, err2=err2 ^ 1), SYNTHETIC_PROFILE)
    with pytest.raises(ValueError, match="serial_id"):
        decode_state(replace(frame, serial_id=0x654321), SYNTHETIC_PROFILE)


@pytest.mark.parametrize("extended", [False, True])
def test_transmit_zero_requires_explicit_permission_for_both_variants(extended):
    profile = SYNTHETIC_PROFILE
    if extended:
        profile = replace(
            profile, protocol_variant="extended_10_word", extended_template=ExtendedFrameTemplate(0x80, 20, 200, 0)
        )
    state = FireplaceState(True, flame=0, fan=6, light=2, front=True, aux=True)
    with pytest.raises(ValueError, match="Flame"):
        encode_state(state, profile)
    frame = encode_state(state, profile, allow_flame_off_when_on=True)
    assert decode_state(frame, profile) == state
    with pytest.raises(ValueError, match="thermostat"):
        encode_state(replace(state, thermostat=True), profile, allow_flame_off_when_on=True)
    with pytest.raises(ValueError, match="power is off"):
        encode_state(replace(state, power=False, flame=1), profile, allow_flame_off_when_on=True)


@pytest.mark.parametrize(
    "changes", [{"fan": 6}, {"light": 2}, {"front": True}, {"aux": False}, {"cpi": False}, {"power": True}]
)
def test_staged_accessory_edits_never_ignite_zero_burner(changes):
    state = FireplaceState(True, flame=0, fan=3, light=6, aux=True, cpi=True)
    assert build_staged_state(FEATURES, state, changes, allow_flame_off_when_on=True) == replace(state, **changes)
    with pytest.raises(StateValidationError, match="flame"):
        build_staged_state(FEATURES, state, changes)


@pytest.mark.parametrize("flame", [-1, 7, 0.5, "0.5", True])
def test_invalid_flame_never_becomes_an_implicit_zero(flame):
    with pytest.raises(StateValidationError):
        build_requested_state(FEATURES, {"power": True, "flame": flame}, allow_flame_off_when_on=True)
    with pytest.raises(StateValidationError):
        build_staged_state(FEATURES, FireplaceState(True, flame=0), {"flame": flame}, allow_flame_off_when_on=True)


def test_normal_power_on_still_ignites_and_explicit_flame_raises_preserve_fields():
    off = FireplaceState(False, flame=0, fan=3, light=2)
    assert build_staged_state(FEATURES, off, {"power": True}, allow_flame_off_when_on=True) == replace(
        off, power=True, flame=1
    )
    zero = replace(off, power=True)
    assert build_staged_state(FEATURES, zero, {"flame": 4}) == replace(zero, flame=4)
    assert build_staged_state(FEATURES, zero, {"power": False}).power is False


def test_firmware_fifo_decoder_accepts_zero_with_original_integrity_checks(tmp_path):
    """Execute the real C++ FIFO decoder, including its ownership filters."""
    root = Path(__file__).resolve().parents[1]
    component = root / "esphome/components/proflame2_tembed"
    cases = []
    for cmd1, err1, cmd2, err2, _flame, _fan, _light in RECORDED_COMMANDS:
        frame = ProflameFrame(0x123456, cmd1, err1, cmd2, err2)
        payload = frame_to_air_bytes(frame)
        cases.append("{" + ",".join(str(x) for x in payload) + "}")
    source = tmp_path / "flame_zero.cpp"
    source.write_text(
        """#include "proflame2_decoder.h"
#include <cassert>
#include <vector>
using namespace esphome::proflame2_tembed;
int main() {
  Proflame2DecodeProfile profile{true, 0x123456, 13, 2, 6, 10};
  std::vector<std::vector<uint8_t>> cases = {"""
        + ",".join(cases)
        + """};
  for (size_t i=0; i<cases.size(); ++i) {
    auto& payload = cases[i];
    Proflame2DecodedPacket packet;
    assert(proflame2_decode_fifo_window(payload.data(), payload.size(), profile, &packet));
    assert(packet.power == 1 && packet.thermostat == 0);
    assert(packet.flame == (i == 0 ? 1 : 0));
    assert(packet.fan == (i < 3 ? 3 : 6));
    assert(packet.light == (i == 2 || i == 4 ? 2 : 6));
    assert(packet.aux == 1 && packet.cpi == 1);
    auto wrong = profile;
    wrong.serial_id++;
    assert(!proflame2_decode_fifo_window(payload.data(), payload.size(), wrong, &packet));
    wrong = profile;
    wrong.d2 ^= 1;
    assert(!proflame2_decode_fifo_window(payload.data(), payload.size(), wrong, &packet));
    auto corrupt = payload;
    corrupt[4] ^= 0x01;
    assert(!proflame2_decode_fifo_window(corrupt.data(), corrupt.size(), profile, &packet));
  }
}"""
    )
    executable = tmp_path / "flame_zero"
    subprocess.run(
        [
            shutil.which("g++"),
            "-std=c++17",
            "-Wall",
            "-Wextra",
            "-Werror",
            f"-I{component}",
            str(source),
            str(component / "proflame2_decoder.cpp"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)
