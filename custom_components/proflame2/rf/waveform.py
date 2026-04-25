"""SmartFire-faithful RF waveform boundary for Proflame2 transmission.

This module exists to keep a clean separation between:

- logical protocol frames: remote ID, command bytes, and Err bytes
- RF serialization details: Manchester symbols, air bytes, repeat policy, and
  eventually radio timing/preamble details

The on-air construction implemented here follows the SmartFire reference:

- https://github.com/JoelB/smartfire/blob/main/smartfire_controller/fireplace.py

Credit:
This module intentionally re-implements the SmartFire project's Proflame2
transmit construction so that later RF backends can send the same logical
waveform structure. The original reference implementation lives at:

- https://github.com/JoelB/smartfire

That source defines three things we can model confidently:

1. Word order on the air:
   serial word 1, serial word 2, serial word 3, cmd1, cmd2, err1, err2
2. Per-word wrapping:
   ``S`` sync symbol, start guard ``1``, 9-bit word, parity bit, end guard ``1``
3. Burst termination:
   nine ``Z`` symbols are appended for separation between bursts

SmartFire also delegates repetition to ``RfCat.RFxmit(..., repeat=repeat)`` and
uses ``repeat = 4`` by default, with an inline comment of ``Default 5
transmissions``. We preserve that behavior as transport metadata, but we do
not invent a numeric inter-repeat spacing because the SmartFire Python source
does not define one. That timing is currently a backend-firmware concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..protocol.packet import ProflameFrame

SYMBOL_TO_BITS = {
    "S": "11",
    "0": "01",
    "1": "10",
    "Z": "00",
}
BITS_TO_SYMBOL = {value: key for key, value in SYMBOL_TO_BITS.items()}
PROFLAME_WORD_COUNT = 7
SYMBOLS_PER_WORD = 13
TRAILING_ZERO_SYMBOLS = 9
TOTAL_SYMBOLS = (PROFLAME_WORD_COUNT * SYMBOLS_PER_WORD) + TRAILING_ZERO_SYMBOLS
AIR_PACKET_BYTES = TOTAL_SYMBOLS * 2 // 8
SMARTFIRE_FIREPLACE_URL = (
    "https://github.com/JoelB/smartfire/blob/main/smartfire_controller/fireplace.py"
)
SMARTFIRE_DEFAULT_RFCAT_REPEAT = 4
SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS = 5


@dataclass(frozen=True)
class ProflameTransmissionPlan:
    """Transmission-ready representation kept separate from the logical frame.

    ``frame`` is the authoritative logical packet.
    ``symbol_string`` is the exact SmartFire symbol sequence before Manchester
    encoding.
    ``air_payload`` is the exact Manchester-encoded byte stream handed to the
    RF backend.

    Repeat handling is modeled as SmartFire does it: the packet bytes are sent
    once and repeated in the RF backend with ``RFxmit(..., repeat=4)``. The
    Python source does not expose a numeric inter-repeat gap, so that remains
    intentionally unspecified here.
    """

    frame: ProflameFrame
    symbol_string: str
    air_payload: bytes
    repeat_count: int = SMARTFIRE_DEFAULT_TOTAL_TRANSMISSIONS
    backend_repeat_argument: int = SMARTFIRE_DEFAULT_RFCAT_REPEAT
    preamble_bytes: bytes = b""
    sync_strategy: str = "embedded_symbol_sync"
    repeat_spacing_ms: float | None = None
    timing_profile: str | None = "rfcat_firmware_repeat"
    source_urls: tuple[str, ...] = (SMARTFIRE_FIREPLACE_URL,)
    notes: tuple[str, ...] = field(
        default_factory=lambda: (
            "SmartFire uses in-band sync symbols and disables modem sync rather than sending an external preamble.",
            "Inter-repeat spacing is not defined in SmartFire Python; repetition is delegated to RfCat.RFxmit firmware.",
            "TODO: validate hardware-observed burst spacing with Yard Stick TX/RX capture before finalizing non-RfCat backends.",
        )
    )


def build_transmission_plan(frame: ProflameFrame) -> ProflameTransmissionPlan:
    """Build a SmartFire-faithful transmit-side plan from a logical frame.

    Credit for the original transmit structure belongs to SmartFire:

    - ``https://github.com/JoelB/smartfire``
    """

    symbol_string = frame_to_symbol_string(frame)

    return ProflameTransmissionPlan(
        frame=frame,
        symbol_string=symbol_string,
        air_payload=symbols_to_air_bytes(symbol_string),
    )


def frame_to_air_bytes(frame: ProflameFrame) -> bytes:
    """Encode a frame into the exact SmartFire Manchester byte stream.

    This function intentionally stops at the same byte-level boundary that
    SmartFire passes into ``RfCat.RFxmit``. It does not invent hardware pulse
    spacing or a software-side repeat cadence.

    Credit for the original byte-stream construction belongs to SmartFire:

    - ``https://github.com/JoelB/smartfire``
    """

    return symbols_to_air_bytes(frame_to_symbol_string(frame))


def frame_to_symbol_string(frame: ProflameFrame) -> str:
    """Build the exact pre-Manchester symbol string used by SmartFire.

    Credit for the original symbol/guard/parity layout belongs to SmartFire:

    - ``https://github.com/JoelB/smartfire``
    """

    words = [
        _word_bits((frame.serial_id >> 16) & 0xFF, trailing_bit=1),
        _word_bits((frame.serial_id >> 8) & 0xFF, trailing_bit=0),
        _word_bits(frame.serial_id & 0xFF, trailing_bit=0),
        _word_bits(frame.cmd1, trailing_bit=0),
        _word_bits(frame.cmd2, trailing_bit=0),
        _word_bits(frame.err1, trailing_bit=0),
        _word_bits(frame.err2, trailing_bit=0),
    ]
    symbols = []
    for word in words:
        parity_bit = str(word.count("1") % 2)
        symbols.extend(["S", "1", word, parity_bit, "1"])
    symbols.append("Z" * TRAILING_ZERO_SYMBOLS)
    return "".join(symbols)


def air_bytes_to_symbols(raw_payload: bytes) -> str:
    """Decode raw Manchester-coded bytes into the Proflame symbol alphabet."""

    bit_stream = "".join(f"{byte:08b}" for byte in raw_payload)
    if len(bit_stream) % 2 != 0:
        raise ValueError("Manchester-coded payload must contain an even number of bits.")

    symbols = []
    for index in range(0, len(bit_stream), 2):
        pair = bit_stream[index : index + 2]
        try:
            symbols.append(BITS_TO_SYMBOL[pair])
        except KeyError as exc:
            raise ValueError(f"Unsupported Manchester symbol pair: {pair}") from exc
    return "".join(symbols)


def symbols_to_air_bytes(symbols: str) -> bytes:
    """Encode the symbol alphabet into Manchester-coded bytes."""

    bit_stream = "".join(SYMBOL_TO_BITS[symbol] for symbol in symbols)
    return bytes(int(bit_stream[index : index + 8], 2) for index in range(0, len(bit_stream), 8))


def _word_bits(byte_value: int, *, trailing_bit: int) -> str:
    """Return the 9-bit on-air word representation used by Proflame 2."""

    return f"{byte_value & 0xFF:08b}{trailing_bit}"
