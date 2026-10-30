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
    missing: tuple[str, ...] = ("No fireplace commands have been received.",)
    _candidate: RemoteProfile | None = field(default=None, init=False, repr=False)
    _basis: frozenset[tuple[int, int]] | None = field(default=None, init=False, repr=False)

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
        if self._basis is not None:
            checks = len(self.frames.keys() - self._basis)
            self.gate = 3 if checks >= 2 else 2 if checks else 1
            return
        if previous_count >= 2:
            return
        qualified = {key for key, count in self.counts.items() if count >= 2}
        result = derive_remote_profile((self.frames[key] for key in qualified), self.features)
        self.missing = result.missing
        if not qualified:
            self.missing = ("Commands need to be received again before they count toward confirmation.",)
        if result.profile is not None:
            self.profile = result.profile
            # Freeze the derivation set; only other combinations confirm it.
            ordered = [key for key in self.frames if key in qualified]
            if frame.is_extended:
                manual = [key for key in ordered if key[0] & 3 == 1]
                first = manual[0]
                selected = [first, next(key for key in manual if key[1] != first[1])]
            else:
                first = ordered[0]
                selected = [
                    first,
                    next(key for key in ordered if key[0] != first[0]),
                    next(key for key in ordered if key[1] != first[1]),
                ]
            selected = list(dict.fromkeys(selected))
            for key in ordered:
                if len(selected) >= 3:
                    break
                if key not in selected:
                    selected.append(key)
            self._basis = frozenset(selected)
            checks = len(self.frames.keys() - self._basis)
            self.gate = 3 if checks >= 2 else 2 if checks else 1

    @property
    def awaiting_repeat(self) -> int:
        """Number of commands observed once rather than at least twice."""
        return sum(count < 2 for count in self.counts.values())

    def progress(self) -> str:
        """Show one actionable count without claiming missing variation is a match."""
        if self.gate == 3:
            return "Your Learning ID is ready."
        if self._basis is not None:
            matches = max(0, 2 - len(self.frames.keys() - self._basis))
            noun = "check" if matches == 1 else "checks"
            return f"{matches} new command {noun} needed to confirm your Learning ID."
        target = 3
        remaining = max(0, target - len(self.frames))
        if remaining:
            return f"{remaining} more needed before your Learning ID is ready."
        repeated = len(self.frames) - self.awaiting_repeat
        matches = max(0, target - repeated)
        if matches:
            noun = "match" if matches == 1 else "matches"
            return f"{matches} {noun} needed to confirm your Learning ID."
        return "Change both Light and Fan, then repeat those settings."

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
                "Two additional different commands are needed to complete confirmation."
            )
        if self.gate == 2:
            return (
                "Confirmation incomplete: The repeated derivation commands agree with this\n"
                "Learning ID, and one additional different command passed validation.\n"
                "One more different command is needed to complete confirmation."
            )
        return (
            f"Confirmed: {len(self._basis or ())} different derivation commands were received at least twice,\n"
            "and at least two additional different commands passed validation. The entire file was checked,\n"
            "and no conflicting commands were found.\nAll required checks passed."
        )
