"""Protocol helpers for Proflame 2 state encoding and decoding."""

from .encoder import encode_state
from .decoder import decode_bytes, decode_state
from .models import ECCProfile, FireplaceFeatures, FireplaceState, RemoteProfile

__all__ = [
    "ECCProfile",
    "FireplaceFeatures",
    "FireplaceState",
    "RemoteProfile",
    "decode_bytes",
    "decode_state",
    "encode_state",
]
