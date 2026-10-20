# Testing and verification

OpenShift has been tested without hardware, in three layers. Run them all with `make -C firmware test`.

| Suite | Tests | What it covers |
|---|---|---|
| `tests/test_shifter.py` | 17 | the gear/sensor core built for the host with trapping UBSan, against exact float oracles; the OpenShift settings, LEDs and buttons |
| `tests/test_emulated.py` | 20 | the real `openshift.bin`, and the stock image, on an emulated board |
| `tests/test_ctl.py` | 9 | `openshift_ctl.py` against the shifter core running in a thread |
| `tests/test_flasher.py` | 19 | the flasher against a simulated bootloader and a fake sysfs tree |

## The emulator (`tests/emu`)

`stm32emu.py` runs a firmware image on unicorn's Cortex-M3 core, with models of:

- the AIR32F103 peripherals the shifter uses:
  - RCC/PLL (including the AIR32 PLL bits) and SysTick (including COUNTFLAG)
  - the NVIC and SCB exception model, and AIRCR reset
  - GPIO and bit-band
  - TIM3 PWM and DMA, used for the LEDs
  - USART1 DMA
  - flash (FPEC, with programming rules checked on every write)
  - the USB device peripheral and its packet memory
- the boot ROM table the stock image calls during PLL setup
- the MLX90393 at pin level (the firmware bit-bangs SPI)
- the buttons and the H/SEQ switch
- a USB host that enumerates like a PC, polls EP1 IN every frame (or skips
  chosen frames, like a slower host), and can send control requests, interrupt OUT reports, bus resets and suspend/resume

The stock image needs no patches to run. That is the basis of the
**differential tests**: the same physical inputs go to stock and to OpenShift,
and everything a PC can see is compared. That covers descriptors, every
standard and class request, both input reports, the UART stream, LED frames
and the settings page contents, byte for byte.

OpenShift's own settings start differently from stock (logo off, a warm
colour), so these tests give both images a settings page with logo on, gamma
off, stock's yellow and full gains in halfwords 0x50 to 0x56, which stock
ignores. The remaining differences are checked explicitly, not skipped: report
0x24 bytes 23, 24, 53 and 54 are cleared after they are checked, OpenShift's
report descriptor (and its length in the HID descriptor) is checked against
the expected one and then replaced with stock's, and LED frames in sequential mode are compared with the handbrake LED
off.

## What matches stock exactly

| Area | Covered by |
|---|---|
| Device, configuration and HID descriptors (except the report descriptor length); strings (except iConfiguration, see below) | `MatchesStock.test_descriptors` |
| Sensor initialisation (the SPI command sequence) | `MatchesStock.test_descriptors` |
| Answers to 22 standard and class requests: GET_STATUS, GET/SET_CONFIGURATION, GET/SET_INTERFACE, SET_IDLE (stalls), GET/SET_PROTOCOL, string/qualifier/HID/report descriptors, unknown vendor requests, SET/CLEAR_FEATURE | `MatchesStock.test_standard_and_class_requests` |
| Gear learning on an uncalibrated unit, 14 poses, with the settings page compared afterwards | `MatchesStock.test_learning_on_an_uncalibrated_unit` |
| Tuner calibration of all 10 targets, then a power cycle; gears 1 to 8; LED colour; auxiliary bytes; switching to sequential; seq up/down; mode button and `24 22`; the handbrake sweep; button A. 54 snapshots of both reports plus both settings pages | `MatchesStock.test_calibration_gears_modes_and_controls` |
| Powering up while in gear | `MatchesStock.test_power_up_in_gear` |
| UART stream with no USB host | `MatchesStock.test_uart_stream_without_usb_host` |
| Host suspend (LEDs and sensor off) and resume | `MatchesStock.test_suspend_and_resume` |

To verify the floating-point details exactly, OpenShift replaces stock's float
library with integer code. `test_shifter.py` checks that code against Python
references for:
- multiply and divide: 100k random cases each
- sqrt: all inputs below 2^16, plus 100k random ones
- `trunc(float(atan2) * 1000)`: 200k cases

These references are exact: double precision, then rounding to float32. Two
real bugs were found this way and fixed:
- `tilt_of` overflowed for s12 ≥ 32768, which a strong magnetic field can produce
- `sh_sincos_q30` had a signed overflow that clang miscompiled

## OpenShift-only behaviour

`OpenShift.*` in `test_emulated.py`:

- enumerates as `10f5:7096` and reports `OpenShift <version>`
- `24 00` turns the LEDs yellow, sets the bootloader flag and resets
- recovery into the bootloader in each of these cases:
  - both buttons (High/low and Handbrake) held at power-up (USB never attaches; one button alone is ignored)
  - a CPU fault
  - a host that resets the bus 8 times without configuring the device
- keeps running with no host at all: UART stream, no spurious recovery
- calibration survives a power cycle, and gears 1 to 8 are then detected
- a sensor that returns only empty frames is powered off and re-initialised after 2 s
- GET_REPORT returns the live reports; control SET_REPORT runs commands
- colour, logo and high/low settings are saved and survive a power cycle; the
  high/low toggle, the handbrake button in H and the handbrake LED in SEQ
- both input reports keep flowing when the host polls only every 2 ms or
  misses frames (0.3.0 failed this: report 0x24 dropped to about 3 per second)

None of these tests may write flash outside the FPEC rules. The emulator records every program and erase and checks it.

## Intentional differences

| Area | Stock | OpenShift |
|---|---|---|
| iConfiguration | 0 (none) | string 4, "OpenShift x.y.z" |
| GET_REPORT | returns a fixed buffer of unrelated RAM | returns the current report 1 or 0x24 |
| Control SET_REPORT | ignored if short; a 64-byte one crashes (jump to 0) | runs the command, like interrupt OUT |
| Recovery | none | High/low and Handbrake buttons held at power-up, fault, 3 s stall, host never configuring, PLL failure: all enter the vendor bootloader |
| System clock | 216 MHz | 72 MHz; delays use stock's effective durations |
| Settings write | read-modify-write of 0x400 halfwords, assuming 2 KB pages | erase, then program only halfwords that need it; works with 1 KB or 2 KB pages; same resulting page |
| Default LED colour | yellow (255 255 0) | 255 182 78 (warm orange), settable with `24 30` |
| LED output | colour × 2/3 | optional gamma 2.2 (`24 34 1`, off by default) and a white balance gain per channel, then × 2/3; gamma off and gains of 255 give stock's output |
| Logo LED | always lit | off unless turned on (`24 31 1`) |
| Handbrake button (B) | only cycles sequential / handbrake; nothing in H | in H, a HID button (report 1 bit 3, button 13); in SEQ as stock |
| Handbrake button LED | lit in every mode; green in handbrake mode | the set colour in H, off in sequential mode, green in handbrake mode |
| High/low button (A) | a button | a button, or a latching toggle (`24 32 1`) with a red/green LED |
| Report descriptor | 11 buttons in nested application collections without a usage; Linux maps them to `BTN_0`.. and udev does not tag a joystick | one Joystick collection with the 12 report bits as buttons 2 to 13, button 1 for the optional neutral button (`24 35 1`), padding, Z and report 0x24 on vendor page `0xFF00` (`OpenShift.test_report_descriptor`); not yet checked on a real host |
| Report 0x24 bytes 23, 24, 53, 54 | 0 | OpenShift options and white balance |
| Settings halfwords 0x50 to 0x56 | unused | OpenShift options, colour and white balance |

Some timings differ by a few tens of milliseconds, all measured in the emulator:

| Event | Stock | OpenShift |
|---|---|---|
| USB attach after reset | about 246 ms | about 235 ms |
| LEDs off after suspend | +1066 ms | +1005 ms |
| LEDs on after resume | +105 ms | +55 ms |
| Sensor start with no host | about 3.45 s | about 3.25 s |

## Not covered

- **Real hardware, beyond a first run.** On 2026-10-20 one unit was flashed
  from Linux with the stock image and then with OpenShift 0.1.0, and OpenShift
  worked. An item-by-item hardware checklist is not done yet.
  The emulator models only what both firmwares use, and its peripheral models
  were written from the STM32F103 reference manual and from what the stock
  image does. The biggest risks are:
  - USB electrical and timing behaviour of the AIR32 USB block
  - the AIR32 PLL at 72 MHz (the stock image only shows its 216 MHz configuration)
  - the flash page size
- **The vendor bootloader.** It is not in any update package, so the flasher
  is tested against a simulator written from Tuner's code. Two real updates
  went through it without errors; an interrupted update has not been tried. See
  [protocol.md](protocol.md#what-is-not-known).
- **The stall watchdog.** The emulator cannot easily hang the main loop.
  The code path is the same `enter_bootloader()` that the fault test uses.
- **The web tool on hardware.** `web/src/lib/lib.test.ts` runs the browser
  updater against a simulated bootloader (`web/src/lib/sim.ts`) that checks
  the erase wait and the gap between pieces. The cases include an interrupted
  write, a cancelled permission prompt and a bootloader whose report
  descriptor has no output report. The real bootloader's report descriptor has
  never been captured, so how WebHID sees it is unknown; the page checks it and
  refuses before erasing if it cannot send report 0x24.

## Running

```sh
pip install -r requirements-dev.txt          # unicorn 2.1.4
make -C firmware                              # builds build/openshift.bin
make -C firmware test                         # all suites (about 4 minutes)
python3 -m unittest discover -s tests -p test_flasher.py -v   # just one suite
```

The differential tests need the stock image at
`firmware/stock/VelocityOneTuner_Shift_Firmware.bin` (or `$OPENSHIFT_STOCK_FW`).
It is not distributed here; without it those tests are skipped.

The web tool has its own tests (Vitest), which also check the image in
`firmware/build/`:

```sh
cd web && npm ci && npm test
```
