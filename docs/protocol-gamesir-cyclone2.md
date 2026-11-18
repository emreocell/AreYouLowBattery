# GameSir Cyclone 2 — vendor HID protocol (XInput dongle)

Device: GameSir Cyclone 2, XInput mode, USB `3537:100b` ("Xbox 360 Controller
for Windows"). Vendor HID = **USB interface 1** (interface 0 is `ff/5d/01`,
claimed by the kernel `xpad` driver, no hidraw node).

Evidence: live probe on a Cyclone 2 over the 2.4 GHz dongle (2026-11-17,
402 × `0x12` frames captured, `captures/probe_gamesir_cyclone2_*.jsonl`),
cross-checked with two independent open source implementations:
`vdemonchy/cyclone2-linux` (`docs/protocol.md`, dongle, battery + RGB) and
`broroeror/gamesir-linux-tools` (`docs/RESEARCH.md`, wired, config + input).

## Transport

Vendor HID reports on the interface-1 hidraw node:

| Report | Direction | Size | Role |
|---|---|---|---|
| `0x0F` | OUTPUT | 64 B | command (wake/battery, register write, RGB) |
| `0x12` | INPUT | 64 B | full controller state (sticks/buttons/battery/charging) |
| `0x10` | INPUT | 64 B | small event report (no battery — parsers must ignore) |

The interface emits **nothing** while idle. Writing any `0x0F` output report
wakes it; after the wake the controller streams `0x12` at a high rate
(~200 Hz observed) until idle again.

## Battery (implemented by `gamesir_v1`)

```
write  0f 03 00 00 00 ... (64 bytes, opcode 0x03, byte[2] = 0x00)
read   12 ...             byte[35] = charging/cable flag (0x01 = plugged)
                          byte[36] = battery percent, raw 0-100
```

- Sample frames: `12 80 80 80 80 0f 00 ...` with b35=`00`, b36=`64` (100 %,
  on dongle, not charging) — sticks rest at `0x80`.
- Levels > 100 are rejected, never clamped or invented. No frames while the
  controller is asleep → last cached value (`Cached (...)` detail) or none.
- `0x10` reports do NOT carry battery (byte 36 is `0x00`).

The RGB lighting protocol rides the same `0x0F` transport with byte[2] =
`0x20` (LED subsystem, registers `0x01` effect, `0x04` brightness,
`0x05/0x08/0x0e/0x11` zones Left/Right/Logo/Center, ~60 ms gap between
writes) — documented by `vdemonchy/cyclone2-linux`; intentionally NOT used by
this handler, which writes only the battery wake frame above.

## Input modes and battery sources

| Mode | USB id | Battery source |
|---|---|---|
| XInput | `3537:100b` | vendor HID `0x12` byte 36 (this handler) |
| DS4 | `054c:09cc` | none — kernel `power_supply` reports a constant ~5 % (`0 × 10 + 5`), the standard input byte 30 stays `0`, and feature `0x12` byte 10 is a frozen snapshot |
| Switch | `057e:2009` | kernel `hid-nintendo` `power_supply`, coarse `capacity_level` (Full/High/Normal/Low/Critical) |
| HID | `3537:0575` | none — also the dongle's own id while the controller is off |

## Safety notes

- `0f 17 55 88` reboots the JieLi BR23 MCU into its UBOOT mass-storage loader —
  never sent by this handler. The 2.4 GHz dongle is a **separate** BR23 chip;
  controller firmware must never be flashed to it (distinguishable in the
  loader by the flash-header product id at offset `0x1010`:
  `GS_C2_ADC_DEVICE` vs `GS_C2_Dongle`).
- On DS4/Switch modes the vendor interface is hidden entirely (firmware
  behaviour, not a Linux limitation).
- The GameSir family register protocol (read `0f 04`, write `0f 03`, banks
  `0x01-0x04` profiles, `0x20` lighting) is shared across models, but framing
  and battery confidence are per-model: this handler is PID-gated to
  `3537:100b` and other models need their own hardware validation before
  being enabled.
