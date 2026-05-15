"""Protocol helpers for Proflame 2 state encoding and decoding."""

from .decoder import decode_bytes, decode_state
from .encoder import encode_state
from .models import ECCProfile, FireplaceFeatures, FireplaceState, RemoteProfile
from .profiles import (
    DEFAULT_PROTOCOL_PROFILE,
    DEFAULT_PROTOCOL_PROFILE_ID,
    GENERIC_PROFLAME2_315,
    PROTOCOL_PROFILES,
    ProtocolFeature,
    ProtocolModulation,
    ProtocolProfile,
    get_protocol_profile,
)

__all__ = [
    "DEFAULT_PROTOCOL_PROFILE",
    "DEFAULT_PROTOCOL_PROFILE_ID",
    "ECCProfile",
    "FireplaceFeatures",
    "FireplaceState",
    "GENERIC_PROFLAME2_315",
    "PROTOCOL_PROFILES",
    "ProtocolFeature",
    "ProtocolModulation",
    "ProtocolProfile",
    "RemoteProfile",
    "decode_bytes",
    "decode_state",
    "encode_state",
    "get_protocol_profile",
]
