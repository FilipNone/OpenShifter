# OpenShift

Open-source replacement firmware for the **Turtle Beach VelocityOne Multi-Shift**
(USB `10f5:7096`), plus a [browser tool](#in-the-browser) and a Linux flasher. Flashing uses the shifter's own vendor
bootloader through the same protocol as VelocityOne Tuner, so no programmer is
needed and the stock firmware can always be put back.

## What it does

OpenShift runs on the same AIR32F103 board as stock firmware TBRS004-20241224
and started from its behaviour. Later versions will change and improve on it.
Version 0.4.0 keeps:

- the same USB device: IDs, strings, HID reports, report 0x24 status and UART stream
- H-pattern gears 1–8, sequential up/down and analog handbrake mode
- the H/SEQ switch
- calibration from VelocityOne Tuner (`24 21 n`), stored in the same settings
  page format, so a calibration made with stock firmware carries over
- gear learning on an uncalibrated unit, using the stock algorithm
- the `24 00` command, which hands over to the vendor bootloader, so Tuner and
  this flasher can update it

It changes the buttons and LEDs. All settings are saved on the shifter and set
with `tools/openshift_ctl.py` (see [Settings and calibration](#settings-and-calibration)):

- **Logo LED** off by default; it can be turned on.
- **LED colour** warm orange (255 182 78) by default; any RGB value can be set.
  The LEDs' blue and green are much brighter than their red, so a white
  balance (a gain per channel, 255 162 96 by default) can be tuned by eye. Gamma correction is optional and off by
  default.
- **Handbrake button** works as a normal button (button 12) while the switch is
  in H. In SEQ it turns handbrake mode on and off, as on stock. Its LED shows
  the set colour in H, is off in sequential mode, and turns green while
  handbrake mode is on.
- **High/low button** is a normal button by default. It can be made a toggle:
  each press flips it on or off, and its LED shows red for off and green for on.

The report descriptor is rewritten so the shifter works as a plain
joystick with the drivers built into Linux, Windows and macOS (checked in the
emulator; a check on real hosts is pending). Stock's
descriptor makes Linux map the buttons outside the joystick range, so udev
doesn't tag the device `ID_INPUT_JOYSTICK`, which SDL, Steam and Wine use to
find game controllers. The `hid-velocityone-multi-shift` driver works around
this with a background program that recreates the joystick through uinput.
OpenShift doesn't need it: gears 1 to 8 are buttons 1 to 8, sequential down
and up are 9 and 10, the high/low button is 11, the handbrake button 12, and
the handbrake axis is Z. The reports themselves, which Tuner reads, are
unchanged. If you run `hid-velocityone-multi-shift`, stop and disable it
after flashing; otherwise games see two joysticks and every press twice.

It also adds things stock doesn't have. A [full list of differences](docs/testing.md#intentional-differences) is in the testing doc.

- **Recovery into the bootloader**, triggered by any of:
  - both buttons held while plugging in
  - any CPU fault
  - a stalled main loop or tick
  - a host that keeps resetting the bus without configuring the device
- A version string (USB iConfiguration, "OpenShift x.y.z") so tools can tell
  which firmware is running.
- Working GET_REPORT and SET_REPORT control requests.

## In the browser

The web tool at **https://filipnone.github.io/OpenShifter/** installs OpenShift,
changes the settings and calibrates the shifter over WebHID, with nothing to
install. It runs in Chrome, Edge and Opera on Windows, macOS, Linux and
ChromeOS; Firefox and Safari do not have WebHID. Everything happens in the
browser, and nothing is uploaded.

- **Firmware:** installs the OpenShift image built from this repository, from
  stock or from an older OpenShift, or a `.bin` file such as Tuner's stock image.
  The first time, the browser asks twice: once for the shifter and once for it in update mode.
  Like every update, this erases the calibration.
- **Settings:** LED colour, logo LED, gamma, white balance and the high/low
  button. The page uses the shifter's LED colour as its accent colour.
- **Calibration:** neutral, handbrake and gears 1 to 8, plus the sequential
  thresholds if you want them.

On Linux the browser needs the same udev rule as the tools below; the page shows
the commands. [`?demo`](https://filipnone.github.io/OpenShifter/?demo) runs the
page against a simulated shifter. Updating from the browser has been tested
against a simulated bootloader only; a test on hardware is pending.

The site is built from `web/` by `.github/workflows/pages.yml`. To work on it:

```sh
make -C firmware                # the site ships firmware/build/openshift.bin
cd web && npm ci
npm run dev                     # http://localhost:5173/, also with ?demo
npm test && npm run build       # dist/
```

## Quick start

Requirements:
- clang and lld (LLVM 15 or newer); no ARM GCC or libc is needed
- Python 3.9 or newer

```sh
make -C firmware                                   # build/openshift.bin
python3 tools/openshift_flash.py info              # read-only: is the shifter there, which firmware?
python3 tools/openshift_flash.py flash --dry-run firmware/build/openshift.bin
sudo python3 tools/openshift_flash.py flash firmware/build/openshift.bin
```

The last command is the same as `make -C firmware flash`. It shows the image and the detected
shifter, asks you to type `flash`, then:
1. switches the shifter to update mode (`10f5:7097`)
2. erases it and writes the image (about 6 s for OpenShift, 12 s for stock)
3. waits for the restart and checks that it now reports `OpenShift x.y.z`

**Every update erases the calibration**, with Tuner too: the vendor bootloader
clears the settings page. Recalibrate afterwards.

To flash without root, install the udev rule once and re-plug the shifter:

```sh
python3 tools/openshift_flash.py udev-rule | sudo tee /etc/udev/rules.d/70-openshift.rules
sudo udevadm control --reload
```

### Going back to the stock firmware

VelocityOne Tuner keeps the vendor image as `VelocityOneTuner_Shift_Firmware.bin`.
The flasher recognises it by its SHA-256:

```sh
python3 tools/openshift_flash.py flash VelocityOneTuner_Shift_Firmware.bin
```

Updating with VelocityOne Tuner on Windows also works, because OpenShift answers
Tuner's update command.

### If something goes wrong

- **Flash interrupted** (cable pulled, error during writing): the shifter
  stays in update mode (`10f5:7097`). Run the same command again; the flasher
  continues from update mode.
- **OpenShift running but misbehaving:** unplug, hold **both buttons**, and
  plug it back in. It starts in update mode; flash any image.
- **OpenShift does not enumerate:** after 6 bus resets that don't lead to
  configuration, it hands over to the bootloader by itself within 5 s.

The bootloader's own behaviour is inferred from Tuner and from the stock
firmware, not observed on hardware. See [docs/protocol.md](docs/protocol.md#what-is-not-known).

## Settings and calibration

`tools/openshift_ctl.py` changes the settings above and calibrates the
shifter. It needs the same access to `/dev/hidraw*` as the flasher (root or
the udev rule). The LED and button settings need OpenShift 0.2.0 or newer, `balance` and
`gamma` 0.3.0;
`status` and `calibrate` also work on stock firmware.

```sh
python3 tools/openshift_ctl.py status               # settings, mode, lever position, calibration
python3 tools/openshift_ctl.py logo on              # or: off
python3 tools/openshift_ctl.py color 255 182 78     # or: color '#ff8000', color default
python3 tools/openshift_ctl.py highlow toggle       # or: button
python3 tools/openshift_ctl.py balance              # guided white balance, by eye (interactive)
python3 tools/openshift_ctl.py balance 255 230 200  # or set the gains R G B, or: default
python3 tools/openshift_ctl.py gamma on             # gamma 2.2 on the LED output; or: off (default)
python3 tools/openshift_ctl.py calibrate            # guided: neutral, handbrake, gears 1-8
python3 tools/openshift_ctl.py calibrate --sequential   # also the sequential up/down thresholds
python3 tools/openshift_ctl.py calibrate gear3      # one target at the current lever position
python3 tools/openshift_ctl.py identify-leds        # light each LED in turn
```

Calibration is the same as Tuner's (`24 21 n`). After calibrating, unplug the
shifter and plug it back in: until it restarts, gear learning keeps adjusting
the gear points.

OpenShift needs no driver beyond the kernel's `hid-generic`. The udev rule is
only for the tools here, which open `/dev/hidraw*`; games read the joystick
through `/dev/input`, which the desktop session already gives the user.

## Repository layout

| Path | Contents |
|---|---|
| `firmware/` | firmware sources (C11, freestanding), linker script, Makefile |
| `tools/openshift_flash.py` | Linux flasher (stdlib only; hidraw + sysfs) |
| `tools/openshift_ctl.py` | settings and calibration (stdlib only) |
| `tests/test_shifter.py` | host build of the gear/sensor logic, checked against float oracles |
| `tests/test_emulated.py` | whole-firmware tests on an emulated board, incl. differential tests against stock |
| `tests/test_ctl.py` | settings tool against the shifter core |
| `tests/test_flasher.py` | flasher against a simulated bootloader and a fake sysfs |
| `tests/emu/` | Cortex-M3 + AIR32F103 + MLX90393 + USB host emulator (unicorn) |
| `web/` | browser tool (React, Headless UI, WebHID) and its tests, published with GitHub Pages |
| `docs/protocol.md` | update protocol, HID reports and commands, settings page |
| `docs/testing.md` | what is verified and how, differences from stock, known gaps |

## Tests

```sh
pip install -r requirements-dev.txt     # unicorn, for the emulator tests only
make -C firmware test                   # or: python3 -m unittest discover -s tests -v
```

The differential tests also need the stock image at
`firmware/stock/VelocityOneTuner_Shift_Firmware.bin` (or `$OPENSHIFT_STOCK_FW`).
It is not distributed here; without it those tests are skipped.

## License

MIT, see [LICENSE](LICENSE). The vendor firmware, Tuner and their contents are
not part of this project.
