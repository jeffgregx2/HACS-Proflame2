# ESPHome/T-Embed CC1101 Transport Contract

This document defines the boundary between the Proflame2 Home Assistant
integration and a LilyGO T-Embed CC1101 ESPHome endpoint.

Home Assistant remains the Proflame2 protocol authority. The T-Embed endpoint is
only a transport and display device.

The endpoint must not own or infer:

- ECC derivation
- Proflame2 command encoding
- profile semantics
- debounce logic
- active profile state
- thermostat policy
- fireplace state authority

The one exception is LilyGO FIFO RX filtering. During guided learning and active
listening the endpoint may decode FIFO byte windows enough to validate candidate
packets against the learned serial/profile. Home Assistant remains the owner of
learning persistence, command generation, and fireplace state policy.

Home Assistant generates the final `TransmissionPlan`. The endpoint transmits
the prepared raw air payload and reports status/results.

## Operation Shape

The T-Embed boundary should stay equivalent in shape to the Yard Stick worker
boundary:

```text
configure_radio(config)
send_payload(request_id, payload, display_metadata?)
get_status()
set_idle()
update_display(display_state)
receive_fifo_window()
set_active_listening(enabled, learned_profile)
close/stop()
```

Yard Stick:

```text
HA -> worker process -> RfCat
```

T-Embed:

```text
HA -> ESPHome/native API -> CC1101
```

Both consume the same HA-generated transmission plan.

## Persistent Radio Configuration

Configuration is persistent endpoint state. It is not repeated on every TX call.

Required fields:

- `config_revision`
- `firmware_protocol_version`
- `tx_frequency_hz`
- `rx_frequency_hz`
- `modulation`
- `data_rate_bps`
- `tx_repeat_count`
- `active_listening_enabled`

Known Proflame2 defaults:

- `tx_frequency_hz`: `314973000`
- `rx_frequency_hz`: `314973000`
- `modulation`: `ask_ook`
- `data_rate_bps`: `2400`
- `tx_repeat_count`: `5`
- `active_listening_enabled`: `false`

Optional/debug fields:

- `inter_frame_gap_ms`
- `rx_bandwidth_hz`
- `sync_mode`
- `packet_mode`
- `debug_enabled`

Do not include `idle_after_tx`. Firmware owns internal CC1101 mode transitions,
FIFO handling, antenna switch behavior, and whether it returns to idle or RX
after TX.

## Status Model

Status enum values:

- `booting`
- `not_configured`
- `configuring`
- `ready`
- `tx_active`
- `rx_active`
- `fault`
- `shutting_down`

Status fields:

- `status`
- `configured`
- `config_revision`
- `firmware_protocol_version`
- `last_error`
- `last_tx_result`
- `last_rx_result`
- `tx_success_count`
- `tx_failure_count`
- `rx_packet_count`
- `uptime_ms`
- `wifi_rssi`
- `ip_address`
- `firmware_version`

## TX Request

Fields:

- `request_id`
- `air_payload` or `air_payload_hex`
- optional `remote_id`
- optional `cmd1`
- optional `err1`
- optional `cmd2`
- optional `err2`
- optional `display_state`

Rules:

- `air_payload` is authoritative.
- Semantic metadata is informational only.
- Firmware must never regenerate or modify Proflame2 payload bytes.
- Firmware sends the payload using configured RF settings.
- Firmware sends exactly `tx_repeat_count` explicit frame transmissions unless
  future configuration explicitly changes repeat behavior.
- Firmware returns to its internally appropriate idle/RX state after TX.

## TX Response

Fields:

- `request_id`
- `ok`
- `payload_length`
- `frames_sent`
- `elapsed_ms`
- `error_code`
- `error_message`
- `radio_status`

## RX Event

The validated LilyGO RX event is a CC1101 FIFO byte-window export. The endpoint
uses the learned serial/profile to suppress nonmatching packets before Home
Assistant sees them. For guided learning, Home Assistant scans accepted FIFO
windows and persists the learned profile. For active listening, firmware
publishes only decoded packets matching the learned profile.

Fields:

- `event_id`
- `timestamp_ms` or `device_tick_ms`
- `fifo_payload_hex`
- `bit_stream`
- `rssi`
- `lqi`
- `frequency_hz`
- `data_rate_bps`
- `rx_fifo_profile`
- `capture_metadata`
- `candidate_scan_result`
- `semantic_fifo_artifact`, if candidate scanning succeeds

Rules:

- Firmware may decode Proflame2 FIFO candidates only to validate learned-profile
  matches and suppress noise/nonmatching remotes.
- Home Assistant remains the semantic owner for learning persistence and
  fireplace state updates.
- RX events are accepted FIFO/semantic packet events, not raw noise events.
- Only host-selected `semantic_fifo_candidate` artifacts with
  `semantic_comparable=true`, `decode_success=true`, and canonical semantic
  witness agreement may be used as packet-owned RX data.
- Raw FIFO windows without a valid candidate remain diagnostic artifacts only.

## Display Update

Fields:

- `fireplace_name`
- `power`
- `flame`
- `fan`
- `light`
- `front`
- `aux`
- `cpi`
- `status_text`
- `fault_text`
- `debug_enabled`

Normal display should be end-user focused:

- fireplace name
- power state
- flame/fan/light/front
- ready/unavailable state
- `Sending...` during TX
- meaningful fault message

Debug display may show:

- frequency
- RSSI/LQI
- IP
- firmware version
- TX/RX counters
- last error

The endpoint must not infer authoritative fireplace state from display data.

## Development RF Witness

Future R820T/`rtl_433` witness tooling should stay development-only:

```text
custom_components/proflame2/rf/dev/
scripts/esphome_tembed_tx_probe.py
scripts/rtl433_witness_probe.py
scripts/compare_yardstick_tembed_tx.py
```

Production code must not import development witness modules.
