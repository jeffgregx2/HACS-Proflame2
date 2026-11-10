"""Validate optional buffered-acquisition health and precise arm-call bounds."""

import re
from collections.abc import Iterable


def read_buffered(lines: Iterable[str]):
    plain, announced, health, bounds = [], {}, {}, {}
    stopped = set()
    for line in lines:
        match = re.search(r"\b(PFBATCH1|PFACQ1|PFBOUND1|PFSTOP1) (.*)", line)
        if match is None:
            plain.append(line)
            continue
        kind, payload = match.groups()
        fields = {}
        for token in payload.strip().split():
            key, separator, value = token.partition("=")
            if not separator or key in fields or not re.fullmatch(r"[0-9]+", value):
                raise ValueError("invalid buffered-acquisition fields")
            fields[key] = int(value)
        session = fields.get("session")
        if session is None:
            raise ValueError("missing buffered-acquisition session")
        if kind == "PFBATCH1":
            if (
                set(fields) != {"session", "slots", "retained_limit", "deferred_export"}
                or fields["slots"] != 2
                or not 1 <= fields["retained_limit"] <= 64
                or fields["deferred_export"] != 1
                or session in announced
            ):
                raise ValueError("invalid buffered-acquisition announcement")
            announced[session] = fields
            continue
        if session not in announced:
            raise ValueError("orphan buffered-acquisition record")
        if kind == "PFSTOP1":
            if set(fields) != {"session", "ended_us"} or session in stopped:
                raise ValueError("invalid buffered-acquisition stop")
            stopped.add(session)
        elif kind == "PFBOUND1":
            identity = session, fields.get("id")
            if (
                set(fields)
                != {"session", "id", "arm_before_us", "arm_after_us", "gpio_anchor_us", "gpio_anchor_observed"}
                or session not in stopped
                or identity in bounds
                or fields["gpio_anchor_observed"] not in (0, 1)
                or fields["arm_before_us"] > fields["arm_after_us"]
            ):
                raise ValueError("invalid buffered-acquisition arm bounds")
            bounds[identity] = fields
        else:
            if (
                set(fields)
                != {
                    "session",
                    "pool_losses",
                    "queue_losses",
                    "callback_losses",
                    "retention_losses",
                    "driver_error",
                    "stack_free_bytes",
                    "ended_us",
                }
                or session in health
                or session not in stopped
            ):
                raise ValueError("invalid buffered-acquisition health")
            if any(
                fields[key]
                for key in ("pool_losses", "queue_losses", "callback_losses", "retention_losses", "driver_error")
            ):
                raise ValueError("buffered capture reported loss or driver failure")
            health[session] = fields
    if set(announced) != set(health):
        raise ValueError("missing buffered-acquisition health")
    return plain, announced, health, bounds
