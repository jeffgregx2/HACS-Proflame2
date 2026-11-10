"""YardStick band policy, independent of protocol frames and ESPHome YAML."""

from dataclasses import dataclass

DEFAULT_RF_BAND = "315"


@dataclass(frozen=True)
class YardStickBand:
    """A supported band and its compatible receive/transmit frequencies."""

    rx_frequency_hz: int
    tx_frequency_hz: int
    minimum_hz: int
    maximum_hz: int

    def learning_frequency(self, override: int | None = None) -> int:
        """Allow diagnostic tuning only inside the controller's selected band."""
        if override is None:
            return self.rx_frequency_hz
        if isinstance(override, bool) or not self.minimum_hz <= override <= self.maximum_hz:
            raise ValueError("YardStick learning frequency override conflicts with the selected RF band.")
        return override


YARDSTICK_BANDS = {
    "315": YardStickBand(315_000_000, 314_973_000, 314_000_000, 316_000_000),
    "433": YardStickBand(433_920_000, 433_920_000, 433_000_000, 435_000_000),
}


def yardstick_band(value: str) -> YardStickBand:
    """Resolve an explicit supported band; never silently fall back on bad input."""
    try:
        return YARDSTICK_BANDS[value]
    except (KeyError, TypeError) as exc:
        raise ValueError("YardStick RF band must be 315 or 433.") from exc
