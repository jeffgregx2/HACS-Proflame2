"""Tests for decoding fireplace frames."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.protocol

from proflame2_protocol.decoder import decode_bytes, decode_state
from proflame2_protocol.encoder import encode_state
from proflame2_protocol.models import FireplaceState
from proflame2_protocol.packet import ProflameFrame


def test_decoder_round_trips_encoded_state(remote_profile) -> None:
    """Encoded state should decode back into the same fields."""

    state = FireplaceState(
        power=True,
        flame=6,
        fan=4,
        light=2,
        front=False,
        aux=True,
        cpi=True,
    )
    frame = encode_state(state, remote_profile)

    assert decode_state(frame, remote_profile) == state
    assert decode_bytes(frame.as_bytes(), remote_profile) == state


def test_decoder_rejects_invalid_err_byte(remote_profile) -> None:
    """Mismatched validation bytes should fail fast."""

    frame = ProflameFrame(
        serial_id=remote_profile.serial_id,
        cmd1=0x01,
        err1=0xFF,
        cmd2=0x11,
        err2=0x22,
    )

    with pytest.raises(ValueError, match="Cmd1 validation byte"):
        decode_state(frame, remote_profile)
