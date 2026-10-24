"""Independent capture-backed RX/TX regressions for every available real remote."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.proflame2.const import PROTOCOL_VARIANT_EXTENDED_10_WORD
from custom_components.proflame2.learning import LearnSession, derive_learn_result_from_session
from custom_components.proflame2.protocol.decoder import decode_state
from custom_components.proflame2.protocol.ecc import (
    build_extended_integrity_words,
    derive_ecc_profile,
    extended_frame_integrity_matches,
)
from custom_components.proflame2.protocol.encoder import encode_state
from custom_components.proflame2.protocol.models import ECCProfile, ExtendedFrameTemplate, RemoteProfile
from custom_components.proflame2.protocol.packet import ProflameFrame, ProflamePacket, state_from_frame
from custom_components.proflame2.rf.fake import FakeRFBackend
from custom_components.proflame2.rf.pulse import find_proflame_pcm_candidates

FIXTURES = Path(__file__).parent / "fixtures"
ALEX = json.loads((FIXTURES / "issue_15_tmfsla_extended_capture.json").read_text())["captures"]
WILLY = json.loads((FIXTURES / "issue_26_mendota_extended_capture.json").read_text())["captures"]
LEGACY = json.loads((FIXTURES / "real_remote_legacy_captures.json").read_text())["remotes"]
VALID = [c for c in ALEX if c["expected_decode"] != "rejected"] + WILLY


def _id(capture: dict) -> str:
    return f"{bytes(capture['words'][:3]).hex()}-{capture['capture_id']}"


def _frame(words: list[int]) -> ProflameFrame:
    return ProflameFrame(
        serial_id=int.from_bytes(bytes(words[:3]), "big"),
        cmd1=words[3],
        cmd2=words[4],
        err1=words[5],
        err2=words[6],
        extension_words=tuple(words[7:]),
    )


def _profile(frame: ProflameFrame) -> RemoteProfile:
    return RemoteProfile(
        serial_id=frame.serial_id,
        ecc=ECCProfile(0, 0, 0, 0),
        protocol_variant=PROTOCOL_VARIANT_EXTENDED_10_WORD,
        extended_template=ExtendedFrameTemplate(
            w4_base=frame.cmd1 & 0x8C,
            w6=frame.err1,
            w7=frame.err2,
            w8=frame.extension_words[0],
        ),
    )


@pytest.mark.parametrize("capture", VALID, ids=_id)
def test_real_extended_capture_integrity(capture: dict) -> None:
    assert extended_frame_integrity_matches(capture["words"])
    words = capture["words"]
    assert build_extended_integrity_words(int.from_bytes(bytes(words[:3]), "big"), *words[3:8]) == tuple(words[8:])


@pytest.mark.parametrize("capture", VALID, ids=_id)
def test_real_extended_raw_pcm_replay(capture: dict) -> None:
    bits = "".join(f"{byte:08b}" for byte in bytes.fromhex(capture["pcm_hex"]))
    candidates = find_proflame_pcm_candidates(bits[: capture["pcm_bit_length"]])
    assert len(candidates) == 1
    assert candidates[0].frame.wire_words == tuple(capture["words"])
    assert candidates[0].extended_integrity_valid is True


@pytest.mark.parametrize("capture", VALID, ids=_id)
def test_real_extended_state_decode_and_manual_tx(capture: dict) -> None:
    frame = _frame(capture["words"])
    profile = _profile(frame)
    state = decode_state(frame, profile)
    assert state.power == bool(frame.cmd1 & 1)
    assert state.flame == frame.cmd2 & 7
    if state.thermostat or (state.power and state.flame == 0):
        # Observed native states are not necessarily supported HA commands.
        with pytest.raises(ValueError):
            encode_state(state, profile, allow_power_off_flame=True)
    else:
        assert encode_state(state, profile, allow_power_off_flame=True).wire_words == frame.wire_words


@pytest.mark.parametrize("remote", LEGACY, ids=lambda r: r["serial_id"])
def test_real_legacy_capture_rx_tx_and_learning_constants(remote: dict) -> None:
    frames = [_frame(words) for words in remote["words"]]
    ecc = derive_ecc_profile([(f.cmd1, f.err1) for f in frames], [(f.cmd2, f.err2) for f in frames])
    assert ecc == ECCProfile(*remote["ecc"])
    profile = RemoteProfile(serial_id=int(remote["serial_id"], 16), ecc=ecc)
    for frame in frames:
        state = decode_state(frame, profile)
        assert encode_state(state, profile, allow_power_off_flame=True).wire_words == frame.wire_words
        with pytest.raises(ValueError, match="Cmd1 validation"):
            decode_state(ProflameFrame(frame.serial_id, frame.cmd1, frame.err1 ^ 1, frame.cmd2, frame.err2), profile)


@pytest.mark.parametrize("capture", [ALEX[0], WILLY[0]], ids=_id)
@pytest.mark.parametrize("word_index", range(10))
@pytest.mark.parametrize("bit", range(8))
def test_each_extended_word_bit_is_protected(capture: dict, word_index: int, bit: int) -> None:
    words = list(capture["words"])
    words[word_index] ^= 1 << bit
    assert not extended_frame_integrity_matches(words)


def test_willy_learning_retains_fixed_control_bits() -> None:
    session = LearnSession(backend=FakeRFBackend(), step_timeout=1.0, receive_timeout=0.1)
    session.remote_id = 0x338705
    session.packets = [ProflamePacket.from_frame(_frame(WILLY[i]["words"])) for i in (0, 2, 17)]
    session.valid_packets = session.packets_seen = len(session.packets)
    result = derive_learn_result_from_session(session)
    assert result is not None and result.success
    assert result.extended_w4_base == 0x84
    assert (result.extended_w6, result.extended_w8) == (0x50, 0x02)


@pytest.mark.parametrize("fixed_bit", [0x04, 0x08, 0x80])
def test_extended_learning_rejects_inconsistent_fixed_control_bits(fixed_bit: int) -> None:
    session = LearnSession(backend=FakeRFBackend(), step_timeout=1.0, receive_timeout=0.1)
    session.remote_id = 0x338705
    first = _frame(WILLY[0]["words"])
    # Give the changed flag valid integrity: learning must reject inconsistent
    # fixed fields, rather than relying on corruption rejection upstream.
    changed = list(WILLY[2]["words"])
    changed[3] ^= fixed_bit
    changed[8:] = build_extended_integrity_words(session.remote_id, *changed[3:8])
    session.packets = [
        ProflamePacket.from_frame(first),
        ProflamePacket.from_frame(_frame(changed)),
        ProflamePacket.from_frame(_frame(WILLY[17]["words"])),
    ]
    session.valid_packets = session.packets_seen = len(session.packets)
    result = derive_learn_result_from_session(session)
    assert result is not None and not result.success
    assert result.error_code == "contradictory_profile"


@pytest.mark.parametrize("thermostat", [False, True])
def test_extended_flame_zero_does_not_rewrite_native_power_bit(thermostat: bool) -> None:
    words = list(WILLY[25]["words"])
    if thermostat:
        words[3] |= 0x02
    words[8:] = build_extended_integrity_words(0x338705, *words[3:8])
    frame = _frame(words)
    state = decode_state(frame, _profile(frame))
    assert state.flame == 0
    assert state.power is True
    assert state.thermostat is thermostat
    with pytest.raises(ValueError):
        encode_state(state, _profile(frame))


def test_legacy_power_on_flame_zero_is_still_rejected() -> None:
    with pytest.raises(ValueError, match="Flame must be between 1 and 6"):
        state_from_frame(ProflameFrame(0x3B3F02, 1, 0x76, 0, 0x18))


def test_corpus_accounts_for_all_four_real_remotes() -> None:
    serials = {bytes(c["words"][:3]).hex().upper() for c in VALID}
    serials.update(r["serial_id"] for r in LEGACY)
    assert serials == {"08E905", "338705", "3B3F02", "0D4602"}
    assert len(WILLY) == 29
    assert len({tuple(c["words"]) for c in WILLY}) == 18
