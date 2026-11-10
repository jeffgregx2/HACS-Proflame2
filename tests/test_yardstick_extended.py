"""Extended FIFO protocol and YardStick transport regressions without RF hardware."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from custom_components.proflame2.learning import (
    ERROR_CONTRADICTORY_PROFILE,
    ERROR_INCONSISTENT_REMOTE_ID,
    LearningModeTransition,
    LearnSession,
    async_capture_next_learning_packet,
    async_learn_remote_profile,
    derive_learn_result_from_session,
)
from custom_components.proflame2.protocol.encoder import encode_packet
from custom_components.proflame2.protocol.models import ECCProfile, ExtendedFrameTemplate, FireplaceState, RemoteProfile
from custom_components.proflame2.protocol.packet import ProflameFrame, ProflamePacket
from custom_components.proflame2.protocol.variants import PROTOCOL_VARIANT_EXTENDED_10_WORD
from custom_components.proflame2.rf.artifacts import is_yardstick_semantic_artifact
from custom_components.proflame2.rf.capture import diagnose_air_payload, find_proflame_candidates
from custom_components.proflame2.rf.fake import FakeRFBackend
from custom_components.proflame2.rf.pulse import decode_pcm_words
from custom_components.proflame2.rf.waveform import (
    build_repeated_air_payload,
    build_transmission_plan,
    frame_to_symbol_string,
    symbols_to_air_bytes,
)
from custom_components.proflame2.rf.yardstick import YARDSTICK_TX_STRATEGY_SOFTWARE_REPEAT, YardStickBackend
from custom_components.proflame2.rf.yardstick_worker import (
    _dispatch_worker_request,
    _YardStickWorkerState,
)
from tools.proflame_capture.yardstick_collector import _normalize_backend_diagnostics

PROFILE = RemoteProfile(
    0x123456,
    ECCProfile(0, 0, 0, 0),
    protocol_variant=PROTOCOL_VARIANT_EXTENDED_10_WORD,
    extended_template=ExtendedFrameTemplate(0x84, 79, 0x16, 3),
)


def packet(flame=3, light=0, profile=PROFILE, power=True):
    # Synthetic native traffic follows the mode recorded in this profile.
    cpi = bool(profile.extended_template and profile.extended_template.w4_base & 0x80)
    result = encode_packet(FireplaceState(power, flame=flame, light=light, cpi=cpi), profile)
    result.transmission_plan = build_transmission_plan(result.frame)
    return result


def test_extended_fifo_roundtrip_preserves_every_word():
    original = packet()
    decoded = find_proflame_candidates(original.transmission_plan.air_payload)
    assert len(decoded) == 1
    assert decoded[0].frame == original.frame
    assert decoded[0].sample.extension_words == original.frame.extension_words


def test_extended_default_burst_has_five_distinct_occurrences():
    original = packet()
    raw, bits = build_repeated_air_payload(original.transmission_plan)
    assert (len(raw), bits) == (169, 1348)
    decoded = find_proflame_candidates(raw)
    assert len(decoded) == 1
    assert decoded[0].repeat_count == 5
    assert len(set(decoded[0].occurrence_offsets)) == 5


def test_extended_bad_integrity_has_a_distinct_rejection():
    frame = packet().frame
    frame = replace(frame, extension_words=(*frame.extension_words[:2], frame.extension_words[2] ^ 1))
    result = diagnose_air_payload(build_transmission_plan(frame).air_payload)
    assert not result.candidates
    assert result.best_failure.reason == "extended_integrity_mismatch"


def test_default_receive_window_fits_extended_frames():
    backend = YardStickBackend(radio=object(), worker_mode=False)
    assert backend._packet_length_bytes == 255


async def test_convenience_learner_supports_extended_profiles():
    backend = FakeRFBackend()
    await backend.connect()
    backend.queue_packets(packet(3, 0), packet(3, 1), packet(4, 1))
    result = await async_learn_remote_profile(backend, timeout=0.02, receive_timeout=0.001)
    assert result.success, result.error
    assert result.protocol_variant == PROTOCOL_VARIANT_EXTENDED_10_WORD
    assert (result.extended_w4_base, result.extended_w6, result.extended_w7, result.extended_w8) == (0x80, 20, 200, 0)
    await backend.close()


def packed(bits):
    return int(bits.ljust((len(bits) + 7) // 8 * 8, "0"), 2).to_bytes((len(bits) + 7) // 8, "big")


@pytest.mark.parametrize("offset", range(8))
def test_fifo_scans_every_bit_alignment(offset):
    original = packet()
    raw = original.transmission_plan.air_payload
    body = "".join(f"{b:08b}" for b in raw)[:260]
    candidates = find_proflame_candidates(packed("0" * offset + body))
    assert len(candidates) == 1
    assert candidates[0].absolute_bit_offset == offset
    assert candidates[0].frame == original.frame
    assert candidates[0].repeat_count == 1


def test_extended_frames_with_same_first_seven_words_do_not_merge():
    first = packet()
    other_profile = replace(PROFILE, extended_template=ExtendedFrameTemplate(0x84, 79, 0x16, 2))
    second = packet(profile=other_profile)
    assert first.frame.wire_words[:7] == second.frame.wire_words[:7]
    raw = first.transmission_plan.air_payload + second.transmission_plan.air_payload
    candidates = find_proflame_candidates(raw)
    assert {c.frame for c in candidates} == {first.frame, second.frame}
    assert [c.repeat_count for c in candidates] == [1, 1]


@pytest.mark.parametrize("word_index", range(10))
def test_complete_extended_integrity_rejects_each_changed_word(word_index):
    words = list(packet().frame.wire_words)
    words[word_index] ^= 1
    changed = ProflameFrame(
        int.from_bytes(bytes(words[:3]), "big"), words[3], words[5], words[4], words[6], tuple(words[7:])
    )
    assert not find_proflame_candidates(build_transmission_plan(changed).air_payload)


@pytest.mark.parametrize("count", [7, 8, 9, 11])
def test_incomplete_or_extra_extended_words_never_fall_back_to_legacy(count):
    symbols = frame_to_symbol_string(packet().frame)[:130]
    malformed = symbols[: count * 13] if count <= 10 else symbols + symbols[13:26]
    # Seven-word prefix is explicitly followed by an incomplete eighth word.
    if count == 7:
        malformed += symbols[91:97]
    assert not find_proflame_candidates(symbols_to_air_bytes(malformed + "Z" * 9))


def test_fifo_never_borrows_rmt_missing_guard_exception():
    bits = "".join(f"{b:08b}" for b in packet().transmission_plan.air_payload)[:259]
    assert decode_pcm_words(bits, 0, 10) is not None
    assert decode_pcm_words(bits, 0, 10, allow_truncated_end_guard=False) is None


def test_mixed_fifo_buffer_and_clipped_prefix_keep_complete_frames():
    extended = packet()
    legacy_profile = RemoteProfile(0x654321, ECCProfile(5, 7, 1, 8))
    legacy = packet(profile=legacy_profile)
    raw = (
        extended.transmission_plan.air_payload[7:]
        + legacy.transmission_plan.air_payload
        + extended.transmission_plan.air_payload
    )
    assert {c.frame for c in find_proflame_candidates(raw)} == {legacy.frame, extended.frame}


class MemoryRadio:
    """A byte queue and recording sink; never opens a device or sends RF."""

    MOD_ASK_OOK = 0x30

    def __init__(self, received=()):
        self.received = list(received)
        self.sent = []
        self.calls = []

    def __getattr__(self, name):
        if name.startswith("set") or name == "makePktFLEN":
            return lambda *args: self.calls.append((name, args))
        raise AttributeError(name)

    def peek(self, address):
        return 0

    def RFrecv(self, timeout=0):
        if not self.received:
            raise TimeoutError()
        return self.received.pop(0), 0

    def RFxmit(self, payload):
        self.sent.append(payload)


class DispatchSupervisor:
    """Exercise actual worker dispatch with an in-memory radio, never rflib."""

    def __init__(self, radio):
        self.state = _YardStickWorkerState(generation=1, device_index=0, mock_mode=True, radio=radio, modulation=0x30)

    def request(self, command, payload=None, *, timeout):
        response, _ = _dispatch_worker_request(self.state, dict(request_id=1, command=command, payload=payload or {}))
        return response

    def stop(self, *, reason="stop"):
        pass


def backend_with_rows(rows, worker=False, **kwargs):
    radio = MemoryRadio(rows)
    backend = YardStickBackend(
        radio=None if worker else radio,
        worker_mode=worker,
        sweep_enabled=False,
        worker_supervisor=DispatchSupervisor(radio) if worker else None,
        **kwargs,
    )
    backend._timeout_exception = TimeoutError
    return backend, radio


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize(
    "method", ["receive", "receive_sample", "receive_diagnostics", "receive_learning_diagnostics", "learn"]
)
async def test_every_receive_entry_point_preserves_extension(worker, method):
    original = packet()
    backend, radio = backend_with_rows([original.transmission_plan.air_payload], worker)
    await backend.connect()
    try:
        result = await getattr(backend, method)(timeout=0.02)
        if method == "receive":
            frame = result.frame
        elif method == "receive_sample":
            frame = result.as_frame()
        elif method == "learn":
            frame = result.frames[0]
        else:
            frame = result.sample.as_frame()
            assert result.decoded_fields["raw_words"] == bytes(original.frame.wire_words).hex()
            assert result.selected_candidate_window["frame_bit_length"] == 260
            assert result.selected_candidate_window["extended_integrity_valid"] is True
            normalized = _normalize_backend_diagnostics(result, worker_diagnostics=None)
            assert normalized.decoded_fields["extension_words"] == list(original.frame.extension_words)
            if method == "receive_learning_diagnostics":
                assert is_yardstick_semantic_artifact(result.semantic_artifact)
                assert result.semantic_artifact["word_count"] == 10
        assert frame == original.frame
        assert ("makePktFLEN", (255,)) in radio.calls
    finally:
        await backend.close()


@pytest.mark.parametrize("worker", [False, True])
@pytest.mark.parametrize("strategy", ["embedded_repeat_payload", YARDSTICK_TX_STRATEGY_SOFTWARE_REPEAT])
async def test_extended_transmit_preserves_burst_and_software_repeat(worker, strategy):
    original = packet()
    backend, radio = backend_with_rows([], worker, tx_repeat_strategy=strategy, tx_frequency_hz=315000000)
    try:
        await backend.send(original)
        expected = (
            [build_repeated_air_payload(original.transmission_plan)[0]]
            if strategy == "embedded_repeat_payload"
            else [original.transmission_plan.air_payload] * 5
        )
        assert radio.sent == expected
        assert ("setFreq", (315000000,)) in radio.calls
        assert radio.calls[-1] == ("setModeIDLE", ())
    finally:
        await backend.close()


async def test_short_diagnostic_capture_window_is_explicit():
    backend, radio = backend_with_rows([b"\x00" * 25], packet_length_bytes=25)
    await backend.connect()
    try:
        result = await backend.receive_diagnostics(timeout=0.01)
        assert not result.decode_success
        assert ("makePktFLEN", (25,)) in radio.calls
        assert any("too short" in item for item in result.diagnostic_limitations)
        assert result.rx_settings["extended_body_fits_window"] is False
    finally:
        await backend.close()


def test_existing_recorded_extended_words_decode_without_changes():
    fixture_root = Path(__file__).parent / "fixtures"
    checked = 0
    for name in ("issue_15_tmfsla_extended_capture.json", "issue_26_mendota_extended_capture.json"):
        for row in json.loads((fixture_root / name).read_text())["captures"]:
            if row.get("expected_decode") == "rejected":
                assert not find_proflame_candidates(bytes.fromhex(row["pcm_hex"]))
                continue
            words = row["words"]
            frame = ProflameFrame(
                int.from_bytes(bytes(words[:3]), "big"), words[3], words[5], words[4], words[6], tuple(words[7:])
            )
            plan = build_transmission_plan(frame)
            recorded = "".join(f"{byte:08b}" for byte in bytes.fromhex(row["pcm_hex"]))[: row["pcm_bit_length"]]
            assert "".join(f"{byte:08b}" for byte in plan.air_payload).startswith(recorded)
            candidates = find_proflame_candidates(plan.air_payload)
            assert len(candidates) == 1
            assert candidates[0].frame.wire_words == tuple(words)
            checked += 1
    assert checked == 120


@pytest.mark.parametrize("worker", [False, True])
async def test_raw_guided_learning_uses_mode_recovery_and_original_diagnostics(worker):
    from custom_components.proflame2.protocol.ecc import build_extended_integrity_words

    original = packet()

    def mode(w4, w5):
        frame = original.frame
        w9, w10 = build_extended_integrity_words(
            frame.serial_id, w4, w5, frame.err1, frame.err2, frame.extension_words[0]
        )
        return replace(frame, cmd1=w4, cmd2=w5, extension_words=(frame.extension_words[0], w9, w10))

    frames = [mode(0x86, 0), mode(0x87, 0), mode(0x84, 3), packet(3).frame, packet(3, 1).frame, packet(4, 1).frame]
    backend, _ = backend_with_rows([build_transmission_plan(f).air_payload for f in frames], worker)
    await backend.connect()
    session = LearnSession(backend=backend, step_timeout=0.1, receive_timeout=0.001)
    try:
        assert await async_capture_next_learning_packet(session) is LearningModeTransition.MANUAL_REQUIRED
        assert await async_capture_next_learning_packet(session) is LearningModeTransition.MANUAL_RESTORED
        assert not session.packets
        for _ in range(3):
            assert isinstance(await async_capture_next_learning_packet(session), ProflamePacket)
        result = derive_learn_result_from_session(session)
        assert result.success
        assert result.extended_w4_base == 0x80
        assert len(session.extended_frame_records) == len(frames)
        assert session.extended_frame_records[-1]["raw_words"] == bytes(frames[-1].wire_words).hex()
        assert session.extended_frame_records[-1]["source"] == "yardstick"
        assert session.extended_frame_records[-1]["frame_bit_offset"] == 0
    finally:
        await backend.close()


async def test_convenience_learner_honors_manual_packet_threshold_and_latest_state():
    varied = replace(PROFILE, extended_template=ExtendedFrameTemplate(0x80, 26, 245, 0))
    observations = [packet(3), packet(3, 1), packet(4, 1), packet(3), packet(3, profile=varied)]
    backend = FakeRFBackend()
    await backend.connect()
    backend.queue_packets(*observations)
    result = await async_learn_remote_profile(backend, timeout=0.05, min_valid_packets=5)
    assert result.success
    assert result.valid_packets == 5
    assert result.final_packet == observations[-1]
    assert (result.extended_w6, result.extended_w7, result.extended_w8) == (20, 200, 0)
    await backend.close()


@pytest.mark.parametrize("fault", ["retained_flags", "identity", "format", "integrity"])
async def test_convenience_learning_rejects_conflicts_before_sufficient_evidence(fault):
    backend = FakeRFBackend()
    await backend.connect()
    first = packet()
    second = packet(4)
    if fault == "retained_flags":
        second = packet(4, profile=replace(PROFILE, extended_template=ExtendedFrameTemplate(0x8C, 79, 0x16, 3)))
    elif fault == "identity":
        second = packet(4, profile=replace(PROFILE, serial_id=0x654321))
    elif fault == "format":
        second = packet(4, profile=RemoteProfile(PROFILE.serial_id, ECCProfile(5, 7, 1, 8)))
    else:
        second = ProflamePacket.from_frame(replace(second.frame, err2=second.frame.err2 ^ 1))
    backend.queue_packets(first, second)
    result = await async_learn_remote_profile(backend, timeout=0.05)
    assert not result.success
    assert result.error_code == (ERROR_INCONSISTENT_REMOTE_ID if fault == "identity" else ERROR_CONTRADICTORY_PROFILE)
    await backend.close()


async def test_units_only_and_thermostat_packets_do_not_satisfy_convenience_learning():
    from custom_components.proflame2.protocol.ecc import build_extended_integrity_words

    first = packet()
    mode = first.frame.cmd1 | 2
    w9, w10 = build_extended_integrity_words(
        PROFILE.serial_id, mode, first.frame.cmd2, first.frame.err1, first.frame.err2, 3
    )
    thermostat = ProflamePacket.from_frame(replace(first.frame, cmd1=mode, extension_words=(3, w9, w10)))
    other = packet(profile=replace(PROFILE, extended_template=ExtendedFrameTemplate(0x80, 26, 245, 0)))
    backend = FakeRFBackend()
    await backend.connect()
    backend.queue_packets(thermostat, first, other, first, other)
    result = await async_learn_remote_profile(backend, timeout=0.01)
    assert not result.success
    assert result.valid_packets == 4
    assert "manual-mode" in result.error
    await backend.close()


async def test_convenience_wrapper_returns_extended_profile_and_releases_backend(hass):
    from custom_components.proflame2.const import DATA_LEARNING_BACKEND_FACTORY, DOMAIN
    from custom_components.proflame2.learning import async_run_learning_with_backend

    backend, _ = backend_with_rows(
        [
            packet(3).transmission_plan.air_payload,
            packet(3, 1).transmission_plan.air_payload,
            packet(4, 1).transmission_plan.air_payload,
        ]
    )
    hass.data.setdefault(DOMAIN, {})[DATA_LEARNING_BACKEND_FACTORY] = lambda _: backend
    result = await async_run_learning_with_backend(hass, "yardstick", timeout=0.1, receive_timeout=0.001)
    assert result.success
    assert result.protocol_variant == PROTOCOL_VARIANT_EXTENDED_10_WORD
    assert backend._shutdown_requested


@pytest.mark.parametrize("symbol_index", [0, 1, 2, 10, 11, 12, 91, 102, 128, 129])
def test_extended_corrupt_guards_parity_and_padding_are_rejected(symbol_index):
    symbols = list(frame_to_symbol_string(packet().frame))
    symbols[symbol_index] = "Z"
    assert not find_proflame_candidates(symbols_to_air_bytes("".join(symbols)))


async def test_ha_guided_flow_learns_from_raw_yardstick_fifo(hass, enable_custom_integrations):
    from homeassistant.config_entries import SOURCE_USER
    from homeassistant.data_entry_flow import FlowResultType

    from custom_components.proflame2.const import (
        CONF_BACKEND_TYPE,
        DATA_LEARNING_BACKEND_FACTORY,
        DATA_LEARNING_RECEIVE_TIMEOUT,
        DATA_LEARNING_TIMEOUT,
        DOMAIN,
    )
    from custom_components.proflame2.protocol.ecc import build_extended_integrity_words

    frames = []
    for w4, w5 in ((0x86, 0), (0x87, 0), (0x84, 3), (0x85, 3), (0x84, 3), (0x85, 3), (0x85, 2)):
        w9, w10 = build_extended_integrity_words(PROFILE.serial_id, w4, w5, 79, 0x16, 3)
        frames.append(ProflameFrame(PROFILE.serial_id, w4, 79, w5, 0x16, (3, w9, w10)))
    backend, _ = backend_with_rows([build_transmission_plan(frame).air_payload for frame in frames])
    hass.data.setdefault(DOMAIN, {})[DATA_LEARNING_BACKEND_FACTORY] = lambda _: backend
    hass.data[DOMAIN][DATA_LEARNING_TIMEOUT] = 0.2
    hass.data[DOMAIN][DATA_LEARNING_RECEIVE_TIMEOUT] = 0.001
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "learn"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Extended FIFO test", CONF_BACKEND_TYPE: "yardstick"}
    )
    assert result["step_id"] == "yardstick_band"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"rf_band": "315"})
    flow_id = result["flow_id"]
    for _ in range(100):
        if result["type"] is FlowResultType.SHOW_PROGRESS:
            flow = hass.config_entries.flow._progress[flow_id]
            task = flow.async_get_progress_task()
            if task is not None:
                await task
                await hass.async_block_till_done()
                result = flow.cur_step
            else:
                result = await hass.config_entries.flow.async_configure(flow_id)
        elif result["type"] is FlowResultType.SHOW_PROGRESS_DONE:
            result = await hass.config_entries.flow.async_configure(flow_id)
        else:
            break
    assert result["step_id"] == "learn_features"
    assert backend._shutdown_requested


@pytest.mark.parametrize("worker", [False, True])
async def test_cancelling_raw_mode_wait_releases_controller(hass, worker):
    import asyncio

    from custom_components.proflame2.const import DATA_LEARNING_BACKEND_FACTORY, DOMAIN
    from custom_components.proflame2.learning import async_close_learning_session, async_start_learning_session
    from custom_components.proflame2.protocol.ecc import build_extended_integrity_words

    w9, w10 = build_extended_integrity_words(PROFILE.serial_id, 0x86, 0, 79, 0x16, 3)
    mode = ProflameFrame(PROFILE.serial_id, 0x86, 79, 0, 0x16, (3, w9, w10))
    backend, _ = backend_with_rows([build_transmission_plan(mode).air_payload], worker)
    hass.data.setdefault(DOMAIN, {})[DATA_LEARNING_BACKEND_FACTORY] = lambda _: backend
    session = await async_start_learning_session(hass, "yardstick", timeout=1, receive_timeout=0.001)
    lease = session.controller_lease
    try:
        assert await async_capture_next_learning_packet(session) is LearningModeTransition.MANUAL_REQUIRED
        task = asyncio.create_task(async_capture_next_learning_packet(session))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await async_close_learning_session(session)
    assert lease.learning_owner is None
    assert backend._shutdown_requested
