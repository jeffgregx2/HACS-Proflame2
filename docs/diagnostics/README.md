# Waveform Diagnostics

This developer-only capability preserves raw LilyGO receive timings and SDR IQ,
coordinates labeled capture sessions, and compares native-remote and controller
transmissions. It is not part of normal device configuration.

## Source Layout

- `esphome/components/proflame2_tembed/diagnostics/`: optional buffered RMT,
  pretrigger history, and independent GPIO-edge observer implementation.
- `esphome/packages/diagnostics/`: explicit receiver-only diagnostic packages.
- `tools/waveform/`: acquisition, coordination, offline analysis, metadata schemas,
  archive verification, and the rtl_433 detector adapter.
- `tests/waveform/`: synthetic acquisition and analysis regression tests, including
  host-side firmware task/buffer tests. No raw field recordings are stored here.

## Firmware

Keep the ordinary base/display packages and external component source on the
same revision. Add these packages **only to a passive debugging receiver**:

```yaml
packages:
  waveform: !include /proflame2/esphome/packages/diagnostics/proflame2_tembed_waveform_diagnostics.yaml
  gpio_edges: !include /proflame2/esphome/packages/diagnostics/proflame2_tembed_waveform_gpio_edges.yaml

proflame2_tembed:
  waveform_session_ms: 90000
```

`/proflame2` is an example container mount; use your checkout's mounted path.
The GPIO observer is optional. Neither package belongs in a normal transmitter
configuration: waveform builds deliberately reject transmission.

The component uses ESPHome's include-directory handling to copy nested diagnostic
sources only when `waveform_diagnostics` is enabled. The implementation and all
production acquisition hooks are additionally protected by compile-time defines.
Removing the packages removes the defines and controls; even sources left by an
incremental build compile without diagnostic implementation. No device-level C++
path configuration is needed. Clean rebuilds are recommended when changing build
roles. Production RX buffers and TX settings are not changed by this reorganization.

## Private Configuration

Store real configuration outside **every** source checkout. Default location:
`$XDG_CONFIG_HOME/proflame2/waveform.env`, or
`~/.config/proflame2/waveform.env` when XDG_CONFIG_HOME is unset.

```bash
mkdir -p ~/.config/proflame2
install -m 600 tools/waveform/waveform.env.example ~/.config/proflame2/waveform.env
```

Edit its placeholders for the capture machine. `PROFLAME2_WAVEFORM_ENV` selects
another external file. Already-exported variables take precedence. Values are
literal `PROFLAME2_NAME=value` entries: no shell execution, interpolation, or
`export` statements. Quote values containing spaces. Actual addresses, device YAML
paths, SDR executable/source locations, and storage paths belong there, not in
tracked files. Keep ESPHome/API credentials in the builder's existing secrets
configuration, not command arguments or manifests.

`PROFLAME2_PILOT_CONFIG` names a separate, private JSON configuration generated
by the configuration tool. This holds reviewed device inventory and recorder
arguments. It also belongs outside source control. Before using an older private
JSON configuration, regenerate it or update its receiver module from
`tools.waveform_docker_logs` to `tools.waveform.waveform_docker_logs`.

If storage is not configured, capture results default to
`$XDG_STATE_HOME/proflame2/waveform` (or `~/.local/state/proflame2/waveform`).
Real sessions are durable evidence: failed/aborted sessions are retained. Do not
store recordings, archives, generated analysis, private configuration, or ELF
files in the repository or `/tmp`. Only disposable build/test scratch uses `/tmp`,
with exit cleanup. Archives can contain private network details; review and
sanitize before sharing them publicly.

## Commands

From the repository root, use a Python environment with `jsonschema` and the
optional analysis dependencies in `tools/waveform/requirements-waveform.txt`.
The local rtl_433 recorder and reviewed rtl_433 source tree are separate
dependencies. No command below sends RF control commands.

```bash
# Create a private JSON configuration, after checking installed recorder options.
python3 -B -m tools.waveform.waveform_pilot_config /private/config/native-pilot.json \
  --gain 0.9 --tool-version VERIFIED-INSTALLED-VERSION --recorder-options-verified

# Review fixed prompts and settings without opening hardware.
tools/waveform/run_waveform_pilot.sh --dry-run

# Native remote capture; follow the terminal's Start / target / Return / Stop prompts.
tools/waveform/run_waveform_pilot.sh --source native --sdr-gain 0.9

# Offline comparison; uses reviewed rtl_433 source configured in waveform.env.
tools/waveform/run_waveform_analysis.sh recording.cu8 receiver.log 1000000 /persistent/analysis
```

For a LilyGO source, select `--source lilygo`; this records the source identity
but never transmits automatically. `--source-tx-offset-hz` records the installed
offset as metadata only. `--gain-check` captures just Light 6 and Light 0.

## Further Reading

- [Detailed capture and analysis procedures](waveform_diagnostics.md)
- [Acquisition design and limitations](waveform_acquisition_design.md)
- [Protocol observations and evidence limits](proflame2_waveform_observations.md)

## Deeper Offline Investigation

The previously private, reusable carrier/grid investigation scripts are preserved
under `tools/waveform/analysis/`, without host identities, run names, or results.
Run them as Python modules. `analyze_manual_tx_log LOG OUTPUT` summarizes local
TX completion and optional retry activity. `audit_low_gain OUTPUT --run RUN`
measures carrier, pulse families, clipping, and lattice coherence for existing
Light 6/0 analysis results; `--reference AUDIT.json` adds prior carrier references.
It reads shared storage directly by default. For optional remote reads, put
`PROFLAME2_CAPTURE_SSH_HOST` and an optional
`PROFLAME2_CAPTURE_SSH_IDENTITY` in the external environment file. It never reads
the key itself or changes SSH host-key verification.

`audit_clusters`, `audit_cluster_controls`, and `audit_cluster_extra` retain the
original specialized 315 MHz, 1 Msps legacy-waveform investigation. These are **not generic
protocol validators**: the first/later-copy controls and offset comparison expect
five-copy bursts. Use newer counts only after adapting and testing that analysis.
For the three-source audit, set `PROFLAME2_ANALYSIS_DATASETS` to an external JSON
mapping with roles `native`, `baseline`, and `candidate`, whose values are run
directory names below the capture root's `runs/` directory. Detector results must
already exist in each run's `analysis-buffered/` (or `analysis-lowgain/` for the
low-gain audit). Outputs remain external private evidence, never committed assets.
`compare_offset_runs BASELINE.json OFFSET.json OUTPUT.json` compares matching
command states and reports uncalibrated relative carrier shifts.

Local TX completion and successful RF decoding do not prove fireplace acceptance.
RMT/GPIO observations are demodulated timing evidence, not calibrated RF amplitude
or carrier measurements. Preserve the original data externally for later replay.
