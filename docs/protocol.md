# VelocityOne Multi-Shift: update protocol and USB interface

Sources:
- the update code in VelocityOne Tuner 1.1.13.0 (.NET; decompiled)
- the stock firmware image TBRS004-20241224 (disassembled, then run in the emulator under `tests/emu`)
- read-only observation of a stock unit

Hardware: an AIR32F103 (STM32F103-compatible Cortex-M3) with an MLX90393 magnetic sensor, RGB LEDs and USB full speed.

## Flash map

| Address | Contents |
|---|---|
| `0x08000000–0x0800EFFF` | vendor bootloader (USB `10f5:7097`), never written by updates |
| `0x0800F000` | settings page, shared with the bootloader |
| `0x08010000–0x0801FFFF` | application, raw vector table first, no header or checksum |

Settings page, as 16-bit halfwords:

| Halfword | Meaning |
|---|---|
| 0 | `1` = stay in the bootloader at the next reset (set by `24 00`) |
| 4 | mode: 0 = H-pattern, 1 = sequential, 2 = handbrake |
| 5, 6 | handbrake rest / pulled y |
| 7 + 4k, 8 + 4k | calibrated x, y of gear k+1 (k = 0..7) |
| 0x27, 0x28 | handbrake rest / pulled x |
| 0x29 | auxiliary flag |
| 0x2a–0x36 | 13 auxiliary bytes (`24 23`) |
| 0x37–0x3e | per-gear match radius |
| 0x3f, 0x40 | sequential thresholds in y: lever pushed forward (positive), pulled back (negative) |
| 0x50 | OpenShift options: high byte `0xA5` marks them as set; bit 0 logo LED on, bit 1 high/low button is a toggle, bit 3 gamma correction on |
| 0x51–0x53 | OpenShift LED colour R, G, B |
| 0x54–0x56 | OpenShift white balance gains R, G, B |
| 0x63 | `0x1254` once calibration is complete |

Stock never reads or writes halfwords 0x41–0x62. OpenShift uses 0x50–0x56.
Without the `0xA5` marker in halfword 0x50 they mean the defaults: logo off,
high/low as a button, colour 255 182 78 (warm orange), gamma off, gains
255 162 96. The marker is there
because the page can come back from an update as zeros, not only as `0xFFFF`:
OpenShift 0.2.0 read a zeroed page as colour 0 0 0, which turned every LED off.

Stock assumes 2 KB flash pages and rewrites halfwords 0–0x3ff. OpenShift preserves everything outside halfwords 0–99.

## Update protocol (bootloader)

Observed on a real unit (stock to stock, flashed from Linux with `tools/openshift_flash.py`, 2026-10-20):

- The bootloader enumerates as `10f5:7097`, bcdDevice 0.03, with the same manufacturer, product and serial strings as the application. It is a HID device (`hid-generic` binds it).
- The whole update took about 13 s from `24 00` until the application was back.
- **An update erases the settings page.** Afterwards stock reported exactly the values it reports from an erased page (checked in the emulator), so the calibration is lost and must be redone. Uncalibrated, stock falls back to gear learning.

Every write is a HID output report with ID `0x24`, 64 bytes including the ID.
Tuner sends it on the interrupt OUT endpoint.

| Device | Write | Effect |
|---|---|---|
| application `10f5:7096` | `24 00` | sets the settings flag and resets into the bootloader, which enumerates as `10f5:7097` |
| bootloader `10f5:7097` | `24 01` | erases the application area; Tuner then waits 2000 ms |
| bootloader | `24 02 iL iH d0…d59` | programs `image[60·i : 60·i+60]`; i is a little-endian int16; the buffer starts as 64 × `0xFF`, so a short last chunk is `0xFF`-padded; Tuner waits 15 ms after each chunk |
| bootloader | `24 03` | update complete; the application starts and enumerates as `10f5:7096` |

Commands are zero-padded to 64 bytes, which is how Windows hidapi sends a
2-byte buffer to a 64-byte report. Tuner does not compute or check an MD5,
checksum or signature anywhere. If Tuner
finds the shifter already in update mode, it starts at `24 01`.

### What is not known

The bootloader was never captured or run (it is not in the update package).
These points are inferred, not observed:

- **Whether it validates an image or reports errors.** Tuner never reads anything back.
- **What clears the stay-in-bootloader flag.** It is probably `24 03`. If so, an interrupted update leaves the unit in update mode, and the flasher's retry advice depends on that.
- **Its report descriptor.** The flasher refuses to erase if `10f5:7097` does not declare output report `0x24`.

## Application USB interface (stock and OpenShift)

The device descriptor:
- `10f5:7096`, bcdDevice 1.11
- manufacturer and product strings "VelocityOne Multi-Shift", including stock's two trailing UTF-16 NULs
- serial number "TBRS004-20241224"

There is one HID interface, with EP1 IN and EP1 OUT, both interrupt and 64 bytes.

The report descriptor differs. Stock's describes the reports below with four
application collections nested in the Joystick collection, none with a usage;
16 bits of buttons for 11 button usages; and report 0x24 as usage 0x01 of the
Generic Desktop page. On Linux that gives buttons `BTN_0` and up instead of
`BTN_TRIGGER` and up, no `ID_INPUT_JOYSTICK` tag from udev, and a `REL_MISC`
axis for report 0x24. OpenShift 0.4.0 and newer send one Joystick
application collection: buttons 1 to 12 and 4 constant padding bits, the
16-bit Z axis, and report 0x24 in and out on vendor page `0xFF00`, which
Linux's HID input driver ignores. The top-level usage (Generic Desktop,
Joystick) and the input and output report IDs and sizes are the same as
stock's. OpenShift 0.4.0 and newer add feature report 0x25 (below).

Input reports, sent every 2 ms:

- **ID 1, 5 bytes:** `01 gears buttons hb_lo hb_hi`.
  - `gears`: bit n is gear n+1 in H-pattern mode.
  - `buttons`: bit 0 is set with the lever pulled back in sequential mode,
    bit 1 with it pushed forward, bit 2 is button A (high/low). Pulling back
    shifts up, so bit 0 is shift up and bit 1 shift down; the firmware
    sources call them `seq_down` and `seq_up` the other way round.
    OpenShift adds bit 3, the handbrake button (B) while the switch is in
    H; its report descriptor declares 12 buttons instead of 11.
    With high/low set to toggle, bit 2 is the latched state.
  - `hb_lo`, `hb_hi`: the 16-bit handbrake axis.
- **ID 0x24, 64 bytes, status:**

  | Byte(s) | Contents |
  |---|---|
  | 1 | `0x20` |
  | 2–3, 4–5 | x, y, big-endian |
  | 6–8 | LED RGB |
  | 9–12 | handbrake rest / pulled y |
  | 13 + 6k | gear k+1 x, y, big-endian |
  | 17, 29, 30, 35, 36, 41, 42, 47 | per-gear radius / 8 |
  | 18 | flags: A, B, H/SEQ switch, handbrake mode, seq down, seq up |
  | 23 | OpenShift only (stock sends 0): bit 7 set, bit 0 logo LED on, bit 1 high/low is a toggle, bit 2 toggle is on, bit 3 gamma on |
  | 24, 53, 54 | OpenShift 0.3.0 and newer (stock sends 0): white balance gains R, G, B |
  | 48 | r / 8: the field strength at the sensor. With x and y it gives the lever's tilt, atan2(√(x² + y²) / 6.55, √(r² − (x² + y²) / 6.55²)) |
  | 59–62 | handbrake rest / pulled x |
  | 63 | mode |

Output report ID 0x24 (64 bytes) commands:

| Command | Effect |
|---|---|
| `24 00` | enter the bootloader (LEDs turn yellow first) |
| `24 20 _ _ _ _ R G B` | LED colour, not saved |
| `24 21 n` | calibrate target n: 1–8 gears, 9 handbrake rest / neutral, 10 handbrake pulled; saved |
| `24 22` | cycle mode (as button B); saved |
| `24 23 b0…b12` | 13 auxiliary bytes; saved |
| `24 24 g v` | per-gear radius byte for gear g = 1..8: shown in report 0x24 only, not stored (stock behaviour) |

OpenShift adds these (stock ignores them):

| Command | Effect |
|---|---|
| `24 30 R G B` | LED colour; saved, and used again after power-up |
| `24 31 v` | logo LED: 0 off, 1 on; saved |
| `24 32 v` | high/low button: 0 a plain button, 1 a toggle (starts off at power-up); saved |
| `24 33 R G B s` | white balance: a gain per channel, 255 = full; saved when s is 1, live only when 0 |
| `24 34 v` | gamma correction: 1 on, 0 off (default); saved |
| `24 3F n` | light only LED n of the chain (0–2) in white, to find which LED is which; `24 3F FF` ends it. Not saved |

`tools/openshift_ctl.py` sends all of these.

Feature report ID 0x25, 16 bytes, OpenShift 0.4.0 and newer: `25` followed by
the version string, for example `0.4.0`, padded with NULs. It is read with
GET_REPORT (feature). Browsers cannot read the iConfiguration string, so the
web tool uses this report to tell OpenShift versions apart. Stock firmware and
older OpenShift versions do not declare it.

LEDs in OpenShift:

| LED | Lit |
|---|---|
| handbrake button | the set colour with the switch in H; off in sequential mode; green in handbrake mode. Stock lights it in every mode, green in handbrake mode |
| high/low button | always; as a toggle, red for off and green for on |
| logo | only when turned on with `24 31 1` |

Lit LEDs that are not green or red use the saved colour, or the live one
from `24 20`. Each
channel then goes through gamma 2.2 if it is turned on (colour values are
perceptual, LED brightness is linear), its white balance gain, then stock's
2/3 brightness cap. The LEDs' blue is much brighter than their red, so without the gains
white and warm colours look blue. On the
WS2812 chain the handbrake LED is first, then the logo, then high/low
(checked on a unit with `24 3F`).

Without a USB host, the unit streams 16-byte packets on USART1 (PA9) every 5 ms:
`24 20 gears flags hb_hi hb_lo R G B 0… 11`.

## Stock firmware quirks worth knowing

- **Delays run a third of their nominal length.** Stock configures `delay_ms()` for a 72 MHz clock but runs the PLL at 216 MHz. OpenShift runs at 72 MHz and uses the effective durations (`STOCK_MS()` in `firmware/src/board.h`).
- **Control SET_REPORT is unsafe on stock.** A 64-byte SET_REPORT crashes stock (it jumps to address 0); short ones are ignored. Send commands on the interrupt OUT endpoint, as Tuner and hidraw do.
- **GET_REPORT returns junk on stock:** a fixed buffer of unrelated RAM.
- **Learning can capture other poses as gears.** Until the unit restarts with a complete calibration, gear learning keeps running. Holding the lever steady at about (−300, −1500), which is the handbrake-pulled target, is learned as gear 4 with a small radius, and the sequential up and down poses as gears 3 and 4. Calibrate targets 9 and 10 and the sequential thresholds before the gears, then restart the unit. `openshift_ctl.py calibrate` uses that order.
- **A unit powered up in gear shows no gear** until the lever has passed through neutral: the first pose seen is taken as neutral.
