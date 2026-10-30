"""Normalize public rtl_433 records into complete, validated protocol frames."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from .derivation import validate_frame
from .packet import ProflameFrame
from .variants import PROTOCOL_VARIANT_EXTENDED_10_WORD, PROTOCOL_VARIANT_LEGACY_7_WORD

MODEL = "Proflame2-Remote"


@dataclass(frozen=True)
class Rtl433Frame:
    """A validated frame with explicit legacy compatibility provenance."""

    frame: ProflameFrame
    legacy_compatibility: bool = False


def _number(record: Mapping, key: str, maximum: int, compatibility: bool) -> int:
    value = record[key]
    if compatibility and isinstance(value, str) and re.fullmatch(r"(?:0x)?[0-9a-fA-F]+", value):
        value = int(value, 16)
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"Invalid numeric field: {key}.")
    return value


def normalize_rtl433_record(record: object) -> Rtl433Frame | None:
    """Ignore unrelated devices; strictly validate Proflame2 fields and integrity.

    Unmarked historical seven-word output follows a dedicated compatibility path.
    Marked records require JSON numbers. Errors never echo captured field values.
    """
    if not isinstance(record, dict):
        raise ValueError("A JSON record must be an object.")
    if record.get("model") != MODEL:
        if "model" in record or not any(key in record for key in ("cmd1", "cmd2", "raw_words")):
            return None
        raise ValueError("A fireplace record is missing its model field.")
    compatibility = "protocol_variant" not in record
    variant = record.get("protocol_variant", PROTOCOL_VARIANT_LEGACY_7_WORD)
    if variant not in (PROTOCOL_VARIANT_LEGACY_7_WORD, PROTOCOL_VARIANT_EXTENDED_10_WORD):
        raise ValueError("Unsupported fireplace protocol format.")
    extended = variant == PROTOCOL_VARIANT_EXTENDED_10_WORD
    tail = ("w6", "w7", "w8", "w9", "w10") if extended else ("err1", "err2")
    if extended and any(key in record for key in ("err1", "err2")):
        raise ValueError("Extended records must not contain legacy error fields.")
    # Extended pilot meaning is not yet established. Preserve compatibility
    # with earlier records that omit it; never infer its value from W4 bit 7.
    if extended and "pilot" in record and (type(record["pilot"]) is not int or record["pilot"] != -1):
        raise ValueError("Extended pilot must be -1 (unknown) until its mapping is established.")
    if not extended and any(key in record for key in ("w6", "w7", "w8", "w9", "w10")):
        raise ValueError("Extended fields require an explicit extended protocol format.")
    expected_mic = "DIGEST" if extended else "PARITY"
    allowed_mic = {expected_mic, "CHECKSUM"} if compatibility else {expected_mic}
    if "mic" in record and record["mic"] not in tuple(allowed_mic):
        raise ValueError("Unexpected integrity label for the fireplace protocol format.")
    if "parity_valid" in record and record["parity_valid"] is not True:
        raise ValueError("The record reports invalid word parity.")

    raw = record.get("raw_words")
    if raw is not None:
        length = 20 if extended else 14
        if not isinstance(raw, str) or re.fullmatch(rf"[0-9a-fA-F]{{{length}}}", raw) is None:
            raise ValueError("Invalid raw word sequence for the fireplace protocol format.")
        words = tuple(bytes.fromhex(raw))
        fields = dict(id=int.from_bytes(bytes(words[:3]), "big"), cmd1=words[3], cmd2=words[4])
        fields.update(zip(tail, words[5:], strict=True))
        for key, value in fields.items():
            if key in record and _number(record, key, 0xFFFFFF if key == "id" else 255, compatibility) != value:
                raise ValueError(f"Raw words disagree with field: {key}.")
    else:
        keys = ("id", "cmd1", "cmd2", *tail)
        if any(key not in record for key in keys):
            raise ValueError("Incomplete fireplace command record.")
        fields = {key: _number(record, key, 0xFFFFFF if key == "id" else 255, compatibility) for key in keys}
        words = (*fields["id"].to_bytes(3, "big"), fields["cmd1"], fields["cmd2"], *(fields[key] for key in tail))

    frame = ProflameFrame(fields["id"], words[3], words[5], words[4], words[6], tuple(words[7:]))
    validate_frame(frame)
    semantics = dict(
        power=words[3] & 1,
        light=(words[3] & 0x70) >> 4,
        thermostat=(words[3] & 2) >> 1,
        front=words[4] >> 7,
        fan=(words[4] & 0x70) >> 4,
        aux=(words[4] & 8) >> 3,
        flame=words[4] & 7,
    )
    if not extended:
        semantics["pilot"] = words[3] >> 7
    for key, value in semantics.items():
        if key in record and _number(record, key, 255, False) != value:
            raise ValueError(f"Raw command bytes disagree with field: {key}.")
    return Rtl433Frame(frame, compatibility)
