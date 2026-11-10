"""Strict transport reader for optional input-only GPIO ISR timestamps.

These timestamps are not calibrated physical edge times. Missing GPIO evidence
is inconclusive: the shared GPIO service is not guaranteed during cache outages.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

CAPACITY = 2048


def _numbers(fields: dict[str, str], expected: set[str]) -> dict[str, int]:
    if set(fields) != expected:
        raise ValueError("invalid GPIO observer record fields")
    if any(not re.fullmatch(r"[0-9]+", value) for value in fields.values()):
        raise ValueError("invalid GPIO observer number")
    return {key: int(value) for key, value in fields.items()}


def edge_checksum(edges: Sequence[int]) -> int:
    value = 2166136261
    for edge in edges:
        for byte in edge.to_bytes(8, "little"):
            value = ((value ^ byte) * 16777619) & 0xFFFFFFFF
    return value


@dataclass(frozen=True)
class GPIOEdgeCapture:
    metadata: dict[str, int]
    encoded_edges: tuple[int, ...]

    def as_json(self) -> dict:
        return {
            "metadata": self.metadata,
            "timestamp_kind": "gpio_isr_service_time_not_calibrated_physical_edge",
            "cache_off_safe": False,
            "encoded_edges": self.encoded_edges,
            "edges": [{"time_us": edge >> 1, "level": edge & 1} for edge in self.encoded_edges],
        }


def read_gpio_edges(lines: Iterable[str]) -> tuple[list[str], set[int], dict[tuple[int, int], GPIOEdgeCapture]]:
    plain, sessions, captures = [], set(), {}
    header = None
    health = None
    edges: list[int] = []
    for line in lines:
        match = re.search(r"\b(PFGPIO1|PFEDGE1) (.*)", line)
        if not match:
            plain.append(line)
            continue
        kind, payload = match.groups()
        fields = {}
        for token in payload.strip().split():
            key, separator, value = token.partition("=")
            if not separator or not value or key in fields:
                raise ValueError("invalid GPIO observer fields")
            fields[key] = value

        if kind == "PFGPIO1":
            item = _numbers(fields, {"session", "capacity", "irq_budget", "irq_window_us", "cache_off_safe"})
            if (
                header is not None
                or item["session"] in sessions
                or item
                != {
                    "session": item["session"],
                    "capacity": CAPACITY,
                    "irq_budget": 32,
                    "irq_window_us": 1000,
                    "cache_off_safe": 0,
                }
            ):
                raise ValueError("invalid GPIO observer announcement")
            sessions.add(item["session"])
        elif "session" in fields:
            if header is not None or set(fields) != {"session", "id", "count", "checksum"}:
                raise ValueError("incomplete or invalid GPIO observer header")
            if not re.fullmatch(r"[0-9a-f]{8}", fields["checksum"]):
                raise ValueError("invalid GPIO observer checksum")
            header = _numbers(
                {key: value for key, value in fields.items() if key != "checksum"}, {"session", "id", "count"}
            )
            header["checksum"] = int(fields["checksum"], 16)
            identity = header["session"], header["id"]
            if header["session"] not in sessions or identity in captures or not 0 <= header["count"] <= CAPACITY:
                raise ValueError("unexpected GPIO observer capture")
            health, edges = None, []
        else:
            if header is None or fields.get("id") != str(header["id"]):
                raise ValueError("orphan GPIO observer record")
            if "request_us" in fields:
                if health is not None:
                    raise ValueError("duplicate GPIO observer health")
                health = _numbers(
                    fields,
                    {
                        "id",
                        "request_us",
                        "start_us",
                        "ready_us",
                        "snapshot_us",
                        "first_seq",
                        "total",
                        "clipped",
                        "raced",
                        "storm",
                        "repeats",
                    },
                )
                if (
                    not 0 <= health["start_us"] <= health["ready_us"] <= health["snapshot_us"]
                    or not 0 <= health["request_us"] <= health["snapshot_us"]
                    or any(health[key] not in (0, 1) for key in ("clipped", "raced", "storm"))
                    or health["first_seq"] + header["count"] != health["total"]
                    or (health["raced"] and header["count"])
                ):
                    raise ValueError("invalid GPIO observer health")
            elif "edges" in fields:
                if (
                    health is None
                    or set(fields) != {"id", "offset", "edges"}
                    or fields["offset"] != str(len(edges))
                    or not re.fullmatch(r"(?:[0-9a-f]{16}){1,8}", fields["edges"])
                ):
                    raise ValueError("invalid GPIO observer chunk")
                chunk = fields["edges"]
                edges.extend(int(chunk[index : index + 16], 16) for index in range(0, len(chunk), 16))
                if len(edges) > header["count"]:
                    raise ValueError("oversized GPIO observer capture")
            elif fields == {"id": str(header["id"]), "end": "1"}:
                if health is None or len(edges) != header["count"] or edge_checksum(edges) != header["checksum"]:
                    raise ValueError("incomplete or corrupted GPIO observer capture")
                times = [edge >> 1 for edge in edges]
                if times != sorted(times) or any(
                    not max(health["request_us"], health["start_us"]) <= time <= health["snapshot_us"] for time in times
                ):
                    raise ValueError("invalid GPIO observer timestamp")
                captures[(header["session"], header["id"])] = GPIOEdgeCapture({**header, **health}, tuple(edges))
                header = None
            else:
                raise ValueError("invalid GPIO observer record")
    if header is not None:
        raise ValueError("incomplete GPIO observer capture")
    return plain, sessions, captures
