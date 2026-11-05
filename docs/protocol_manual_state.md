# Proflame2 manual power and flame state

Power and flame are independent fields: Cmd1 bit 0 is power and Cmd2 bits 0–2
are flame. In the extended layout these are W4 and W5 respectively. Thermostat
is Cmd1/W4 bit 1. This reference concerns manual mode; it does not add thermostat
control.

## Confirmed legacy flame-zero behavior

The [Issue #33 captures](https://github.com/jeffgregx2/HACS-Proflame2/issues/33#issuecomment-6643963545),
reviewed on 2026-11-04, contain integrity-consistent seven-word manual frames
with **power ON and flame 0**. Flame 0 therefore does not universally imply
power OFF. Two sequences retain power ON/flame 0 while changing light 6 to 2,
once with fan 3 and once with fan 6. AUX (Cmd2 bit 3) and CPI (Cmd1 bit 7)
remain set. No additional mode bit is required by these sequences.

| Transition | Cmd1 | Cmd2 | Meaning |
| --- | --- | --- | --- |
| Before lowering flame | E1 | 39 | ON, flame 1, light 6, fan 3, AUX/CPI ON |
| Lower flame to zero | E1 | 38 | ON, flame 0; accessories unchanged |
| Adjust light | A1 | 38 | ON, flame 0, light 2; other fields unchanged |
| Higher-fan example | E1 | 68 | ON, flame 0, light 6, fan 6, AUX/CPI ON |
| Adjust light | A1 | 68 | ON, flame 0, light 2; other fields unchanged |

Original command/integrity pairs are recorded in the
[implementation design](issue_33_flame_off_design.md#resolved-packet-evidence).
Raw recordings and remote identity are not published here. Structural/parity,
identity, and original integrity validation remain required; semantic acceptance
of zero must not bypass them.

This confirms transmitted fields, not physical pilot operation, continuous fan
operation, or front-burner behavior. Some fireplaces instead shut down at flame
0 or ignore accessory controls in that state. No universal capability can be
inferred from brand, band, identity, or protocol variant.

## Integration policy

- Report an integrity-valid matching ON/zero observation accurately regardless
  of transmit permission, for both legacy and extended frames.
- Sending manual ON/zero requires **Allow flame OFF while power is ON**,
  disabled by default. Preserve zero through accessory edits and state replay;
  never silently substitute flame 1. Without permission, reject that command.
- A normal OFF-to-ON action still uses the retained positive flame or level 1.
  A redundant ON action while already ON/zero preserves zero.
- Learning IDs and legacy C/D integrity constants are unaffected. The option
  belongs to fireplace configuration, not remote identity.

See the [extended protocol reference](issue_15_extended_protocol_design.md) for
ten-word integrity and temperature fields. The new Issue #33 sequence establishes
legacy behavior; it does not newly establish extended CPI semantics.
