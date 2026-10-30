"""Deterministic command/repeat accounting and frozen-profile confirmation."""

from __future__ import annotations

from dataclasses import dataclass, field

from proflame2_protocol.derivation import LearningRequirements, ProfileConflictError, derive_remote_profile
from proflame2_protocol.models import FireplaceFeatures, RemoteProfile
from proflame2_protocol.packet import ProflameFrame

SINGLE_FRAME = LearningRequirements(min_frames=1, min_commands=1, min_cmd1=1, min_cmd2=1, min_manual_w5=1)


@dataclass
class Evidence:
    """Validate every frame, retaining bounded command representatives, not logs."""

    features: FireplaceFeatures
    frames: dict[tuple[int, int], ProflameFrame] = field(default_factory=dict)
    counts: dict[tuple[int, int], int] = field(default_factory=dict)
    profile: RemoteProfile | None = None
    gate: int = 0
    gate_commands: set[tuple[int, int]] = field(default_factory=set)
    missing: tuple[str, ...] = ("No fireplace commands have been received.",)
    _candidate: RemoteProfile | None = field(default=None, init=False, repr=False)

    def add(self, frame: ProflameFrame) -> None:
        """Reject contradictions before deduplication, including unmatched commands."""
        first = next(iter(self.frames.values()), None)
        if first is not None and (first.serial_id != frame.serial_id or first.is_extended != frame.is_extended):
            raise ProfileConflictError("Commands must come from one remote and one protocol format.")
        # Validate independently against the candidate rather than re-solving
        # every previous observation on every repeated frame in a long file.
        candidate = derive_remote_profile((frame,), self.features, requirements=SINGLE_FRAME).profile
        if self._candidate is not None and candidate is not None and candidate != self._candidate:
            raise ProfileConflictError("Additional commands contradict the previously derived profile.")
        if self._candidate is None:
            self._candidate = candidate
        key = (frame.cmd1, frame.cmd2)
        if key not in self.frames and len(self.frames) >= 4096:
            raise ValueError("Too many different commands; input limit reached.")
        self.frames.setdefault(key, frame)
        previous_count = self.counts.get(key, 0)
        self.counts[key] = min(2, previous_count + 1)
        if previous_count >= 2 or self.gate == 3:
            return
        qualified = {key for key, count in self.counts.items() if count >= 2}
        result = derive_remote_profile((self.frames[key] for key in qualified), self.features)
        self.missing = result.missing
        if not qualified:
            self.missing = ("Commands need to be received again before they count toward confirmation.",)
        if self.gate == 0 and result.profile is not None:
            self.profile = result.profile
            self.gate = 1
            self.gate_commands = qualified
        elif self.gate in (1, 2) and len(qualified - self.gate_commands) >= 2:
            self.gate += 1
            self.gate_commands = qualified

    @property
    def awaiting_repeat(self) -> int:
        """Number of commands observed once rather than at least twice."""
        return sum(count < 2 for count in self.counts.values())

    def progress(self) -> str:
        """Explain actual evidence counts without exposing captured values."""
        target = 7 if not self.gate else len(self.gate_commands) + 2 * (3 - self.gate)
        remaining = max(0, target - len(self.frames))
        lines = [
            f"{len(self.frames)} different commands received.",
            f"{remaining} more needed before your Learning ID is ready.",
            f"{self.awaiting_repeat} commands still need to be received again.",
        ]
        lines.extend(self.missing)
        return "\n".join(lines)

    def file_summary(self) -> str:
        """Always explain what the final file-mode confirmation level means."""
        repeated = len(self.frames) - self.awaiting_repeat
        if self.gate == 0:
            return (
                "No Learning ID was generated.\n"
                "This file does not contain enough different commands with matching repeats.\n"
                + "\n".join(self.missing)
            )
        if self.gate == 1:
            return (
                f"Preliminary: A Learning ID was generated from {repeated} different commands.\n"
                "The commands agree, and each was received at least twice, but this file\n"
                "does not contain enough different commands to complete confirmation."
            )
        if self.gate == 2:
            return (
                f"Confirmation incomplete: {repeated} different commands agree with this\n"
                "Learning ID, and each was received at least twice.\n"
                "Additional checks passed, but full confirmation requires at least 7 different\n"
                "commands, each received at least twice."
            )
        return (
            f"Confirmed: {repeated} different commands agree with this Learning ID,\n"
            "and each was received at least twice. The entire file was checked,\n"
            "and no conflicting commands were found.\nAll required checks passed."
        )
