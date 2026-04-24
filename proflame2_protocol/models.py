"""Domain models for Proflame 2 fireplaces."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ECCProfile:
    """Stable ECC constants learned from a handheld remote."""

    c1: int
    d1: int
    c2: int
    d2: int


@dataclass(frozen=True)
class FireplaceFeatures:
    """User-declared fireplace feature support."""

    fan: bool = True
    light: bool = True
    front: bool = False
    aux: bool = False
    cpi: bool = False


@dataclass(frozen=True)
class RemoteProfile:
    """Learned remote identity and profile constants."""

    serial_id: int
    ecc: ECCProfile
    features: FireplaceFeatures = field(default_factory=FireplaceFeatures)


@dataclass(frozen=True)
class FireplaceState:
    """Manual-state fireplace command model."""

    power: bool
    flame: int = 1
    fan: int = 0
    light: int = 0
    front: bool = False
    aux: bool = False
    thermostat: bool = False
    cpi: bool = False

    def validate(self, *, allow_thermostat: bool = False) -> None:
        """Validate a state against the current v1 policy."""
        if not self.power:
            if self.flame != 0:
                raise ValueError("Flame must be 0 when power is off.")
        elif self.thermostat and allow_thermostat:
            if not 0 <= self.flame <= 6:
                raise ValueError("Thermostat flame must be between 0 and 6.")
        elif not 1 <= self.flame <= 6:
            raise ValueError("Flame must be between 1 and 6 when power is on.")

        if not 0 <= self.fan <= 6:
            raise ValueError("Fan must be between 0 and 6.")

        if not 0 <= self.light <= 7:
            raise ValueError("Light must be between 0 and 7.")

        if self.thermostat and not allow_thermostat:
            raise ValueError("Native thermostat mode is disabled for v1.")
