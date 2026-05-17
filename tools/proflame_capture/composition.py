"""Collector composition helpers for coordinated capture sessions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any

from tools.proflame_capture.collectors import (
    StubLilyGoCollector,
    StubRtl433Collector,
    StubYardStickCollector,
)
from tools.proflame_capture.lilygo_syslog import LilyGoSyslogCollector
from tools.proflame_capture.rtl433_collector import Rtl433Collector
from tools.proflame_capture.yardstick_collector import (
    InjectedYardStickDiagnosticSource,
    YardStickDiagnosticCollector,
)


@dataclass
class CollectorPlanEntry:
    source_name: str
    mode: str
    collector: object
    config: dict[str, Any]


def build_collector_plan(
    args: argparse.Namespace,
    *,
    overrides: dict[str, CollectorPlanEntry] | None = None,
) -> list[CollectorPlanEntry]:
    plan: list[CollectorPlanEntry] = []
    overrides = overrides or {}

    lilygo_entry = overrides.get("lilygo") or _build_lilygo_entry(args)
    if lilygo_entry is not None:
        plan.append(lilygo_entry)

    rtl433_entry = overrides.get("rtl433") or _build_rtl433_entry(args)
    if rtl433_entry is not None:
        plan.append(rtl433_entry)

    yardstick_entry = overrides.get("yardstick") or _build_yardstick_entry(args)
    if yardstick_entry is not None:
        plan.append(yardstick_entry)

    names = [entry.source_name for entry in plan]
    if len(names) != len(set(names)):
        raise ValueError("duplicate collector source selection")
    return plan


def summarize_plan(plan: list[CollectorPlanEntry]) -> dict[str, object]:
    return {
        "selected_collectors": [entry.source_name for entry in plan],
        "collector_modes": {entry.source_name: entry.mode for entry in plan},
        "collector_config": {entry.source_name: entry.config for entry in plan},
    }


def render_plan_lines(args: argparse.Namespace, plan: list[CollectorPlanEntry]) -> list[str]:
    lines = [
        f"Capture plan: output_root={args.output_root} valid_target={args.valid_samples_target} max_attempts={args.max_attempts} prearm_timeout={args.prearm_timeout_seconds}s sample_timeout={args.sample_timeout_seconds}s",
        "  initial_state=unknown; rtl_433 decoded state is canonical",
        f"  commands={','.join(args.command) if args.command else 'power_toggle,flame_up,flame_down,fan_up,fan_down'}",
    ]
    for entry in plan:
        if entry.source_name == "lilygo":
            if entry.mode == "syslog":
                lines.append(
                    f"  - lilygo [{entry.mode}] udp={entry.config['bind_host']}:{entry.config['bind_port']} source_host={entry.config['source_host_filter'] or '*'}"
                )
            else:
                lines.append(f"  - lilygo [{entry.mode}]")
        elif entry.source_name == "rtl433":
            if entry.mode == "subprocess":
                lines.append(
                    f"  - rtl433 [{entry.mode}] cmd={entry.config['executable_path']} -f {entry.config['frequency']} -g {entry.config['gain']} -R {entry.config['protocol']}"
                )
            else:
                lines.append(f"  - rtl433 [{entry.mode}]")
        elif entry.source_name == "yardstick":
            lines.append(f"  - yardstick [{entry.mode}]")
    return lines


def _build_lilygo_entry(args: argparse.Namespace) -> CollectorPlanEntry | None:
    if args.lilygo_syslog:
        config = {
            "bind_host": args.lilygo_syslog_host,
            "bind_port": args.lilygo_syslog_port,
            "source_host_filter": args.lilygo_source_host,
        }
        return CollectorPlanEntry(
            source_name="lilygo",
            mode="syslog",
            collector=LilyGoSyslogCollector(
                bind_host=args.lilygo_syslog_host,
                bind_port=args.lilygo_syslog_port,
                source_host_filter=args.lilygo_source_host,
            ),
            config=config,
        )
    if args.stub_sources:
        return CollectorPlanEntry(
            source_name="lilygo",
            mode="stub",
            collector=StubLilyGoCollector(),
            config={"stub": True},
        )
    return None


def _build_rtl433_entry(args: argparse.Namespace) -> CollectorPlanEntry | None:
    if args.rtl433:
        config = {
            "executable_path": args.rtl433_path,
            "frequency": args.rtl433_frequency,
            "gain": args.rtl433_gain,
            "protocol": args.rtl433_protocol,
            "extra_args": list(args.rtl433_extra_arg),
        }
        return CollectorPlanEntry(
            source_name="rtl433",
            mode="subprocess",
            collector=Rtl433Collector(
                executable_path=args.rtl433_path,
                frequency=args.rtl433_frequency,
                gain=args.rtl433_gain,
                protocol=args.rtl433_protocol,
                extra_args=args.rtl433_extra_arg,
            ),
            config=config,
        )
    if args.stub_sources:
        return CollectorPlanEntry(
            source_name="rtl433",
            mode="stub",
            collector=StubRtl433Collector(),
            config={"stub": True},
        )
    return None


def _build_yardstick_entry(args: argparse.Namespace) -> CollectorPlanEntry | None:
    if args.yardstick:
        if args.yardstick_mode == "stub":
            return CollectorPlanEntry(
                source_name="yardstick",
                mode="stub",
                collector=StubYardStickCollector(),
                config={"mode": "stub"},
            )
        if args.yardstick_mode == "injected":
            return CollectorPlanEntry(
                source_name="yardstick",
                mode="injected",
                collector=YardStickDiagnosticCollector(
                    mode="injected",
                    source=InjectedYardStickDiagnosticSource(),
                ),
                config={"mode": "injected"},
            )
        return CollectorPlanEntry(
            source_name="yardstick",
            mode="live",
            collector=YardStickDiagnosticCollector(mode="live"),
            config={"mode": "live"},
        )
    if args.stub_sources:
        return CollectorPlanEntry(
            source_name="yardstick",
            mode="stub",
            collector=StubYardStickCollector(),
            config={"stub": True},
        )
    return None
