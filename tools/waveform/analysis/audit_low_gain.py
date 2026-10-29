"""Offline low-gain replay; retrieve bounded slices through the capture owner.

Remote access only reads retained files. No SDR hardware or firmware is opened.
Keep output private: it contains original capture identifiers and raw IQ slices.
"""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
from itertools import accumulate
from pathlib import Path

import numpy as np

from tools.waveform.analysis.audit_clusters import fft_metrics, pulse_groups, raw_widths
from tools.waveform.environment import load_environment
from tools.waveform.waveform_cluster import best_lattice, crossing_edges, lattice, signed_runs, smooth_power, summary
from tools.waveform.waveform_pretrigger_review import structural_frames


def remote_bytes(path, start=None, end=None):
    code = (
        "import pathlib,sys; "
        "f=pathlib.Path(sys.argv[1]).open('rb'); data=f.read(16*1024*1024+1); "
        "assert len(data)<=16*1024*1024, 'metadata exceeds size limit'; sys.stdout.buffer.write(data)"
    )
    args = [str(path)]
    if start is not None:
        if not 0 <= start < end or end - start > 2_000_000:
            raise ValueError("invalid bounded IQ slice")
        code = (
            "import pathlib,sys; f=pathlib.Path(sys.argv[1]).open('rb'); "
            "f.seek(int(sys.argv[2])*2); "
            "sys.stdout.buffer.write(f.read((int(sys.argv[3])-int(sys.argv[2]))*2))"
        )
        args.extend([str(start), str(end)])
    command = shlex.join(["python3", "-B", "-c", code, *args])
    host = os.environ.get("PROFLAME2_CAPTURE_SSH_HOST")
    if host:
        if host.startswith("-") or any(char.isspace() for char in host):
            raise ValueError("invalid capture SSH host")
        ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
        identity = os.environ.get("PROFLAME2_CAPTURE_SSH_IDENTITY")
        if identity:
            ssh.extend(["-o", "IdentitiesOnly=yes", "-i", str(Path(identity).expanduser())])
        data = subprocess.check_output([*ssh, host, command], timeout=60)
    else:
        with Path(path).open("rb") as source:
            if start is not None:
                source.seek(start * 2)
            data = source.read((end - start) * 2 if start is not None else 16 * 1024 * 1024 + 1)
        if start is None and len(data) > 16 * 1024 * 1024:
            raise ValueError("metadata exceeds size limit")
    if start is not None and len(data) != (end - start) * 2:
        raise ValueError("short remote IQ slice")
    return data


def main(output, run, *, save_iq=False, reference=None):
    load_environment()
    output.mkdir(mode=0o700)
    rows = []
    gain_reports = {}
    for trial in ("trial-001-light_6", "trial-002-light_0"):
        events = json.loads(remote_bytes(run / "analysis-lowgain" / trial / "events.json"))
        gain_reports[trial] = json.loads(remote_bytes(run / "session" / trial / "gain-check.json"))
        for event in events:
            start, end = event["start_sample"], event["end_sample"]
            data = remote_bytes(run / "session" / trial / "signal.cu8", start - 5000, end + 5000)
            filename = f"{trial}-event-{event['event_id']:03d}.cu8"
            if save_iq:
                (output / filename).write_bytes(data)
            iq = np.frombuffer(data, dtype=np.uint8).reshape(-1, 2).astype(float) - 127.5
            z = iq[:, 0] + 1j * iq[:, 1]
            p = smooth_power(z, 5)
            off, on = np.quantile(p, [0.1, 0.9])
            high = p > off + 0.5 * (on - off)
            rail = (np.abs(z.real) == 127.5) | (np.abs(z.imag) == 127.5)
            times = np.asarray(list(accumulate(map(abs, event["signed_samples"]), initial=0)))
            levels = np.arange(len(times)) % 2 == 0
            tick = best_lattice([(times, levels)], np.linspace(52.08, 52.14, 601))
            raw_times, raw_levels = crossing_edges(p, float(off + 0.5 * (on - off)))
            raw_runs = signed_runs(raw_times, raw_levels)
            raw_groups, _ = pulse_groups(raw_runs)
            detector_groups, _ = pulse_groups(event["signed_samples"])
            frames = structural_frames(event["signed_samples"])
            widths = [
                {"threshold_fraction": fraction, "widths": raw_widths(p, float(off + fraction * (on - off)))}
                for fraction in (0.2, 0.35, 0.5, 0.65, 0.8)
            ]
            row = {
                "trial": trial,
                "event_id": event["event_id"],
                "start_sample": start,
                "end_sample": end,
                "slice": {
                    "filename": filename,
                    "start_sample": start - 5000,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    "saved": save_iq,
                },
                "rail_count_all": int(rail.sum()),
                "rail_fraction_on": float(rail[high].mean()),
                "on_power_p90": float(on),
                "off_power_p10": float(off),
                "fft": fft_metrics(z[5000:-5000]),
                "detector_tick": tick,
                "raw_half_power_tick": lattice(raw_times, raw_levels, tick["period_us"]),
                "structural_frame_count": len(frames),
                "wire_words": sorted({tuple(frame["wire_words"]) for frame in frames}),
                "raw_half_power_widths": {key: summary(value) for key, value in raw_groups.items()},
                "detector_widths": {key: summary(value) for key, value in detector_groups.items()},
                "threshold_controls": widths,
            }
            rows.append(row)
            print(f"Measured {trial} burst {event['event_id']} of {len(events)}", flush=True)
    previous = json.loads(reference.read_text()) if reference else {"datasets": {}}
    carriers = [row["fft"]["local_500hz_centroid_hz"] for row in rows]
    results = {
        "source_run": run.name,
        "source_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "numpy_version": np.__version__,
        "gain_reports": gain_reports,
        "events": rows,
        "metrics": {
            "carrier_relative_center_hz": summary(carriers),
            "detector_tick_us": summary([r["detector_tick"]["period_us"] for r in rows]),
            "detector_tick_coherence": summary([r["detector_tick"]["coherence"] for r in rows]),
            "raw_half_power_tick_coherence": summary([r["raw_half_power_tick"]["mean_coherence"] for r in rows]),
            "full_burst_rail_count": sum(r["rail_count_all"] for r in rows),
            "structural_frames": sum(r["structural_frame_count"] for r in rows),
        },
        "prior_carriers": {
            key: value["metrics"]["carrier_relative_center_hz"] for key, value in previous["datasets"].items()
        },
        "limitations": [
            "Receiver time and SDR oscillator remain uncalibrated.",
            "Prior reference recordings were taken earlier; oscillator drift is not excluded.",
            "No fireplace acceptance labels; no demonstrated causal reception fix.",
            "Different thresholds shift measured edges; no GPIO timing is inferred directly.",
        ],
    }
    (output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({"metrics": results["metrics"], "prior_carriers": results["prior_carriers"]}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--reference", type=Path, help="optional prior clustering audit for carrier comparison")
    parser.add_argument(
        "--save-iq-slices", action="store_true", help="retain duplicate slices; otherwise reference original IQ"
    )
    args = parser.parse_args()
    main(args.output, args.run, save_iq=args.save_iq_slices, reference=args.reference)
