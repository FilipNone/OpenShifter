# OpenShift

Open-source replacement firmware for the **Turtle Beach VelocityOne Multi-Shift**
(USB `10f5:7096`), plus a Linux flasher. Flashing uses the shifter's own vendor
bootloader through the same protocol as VelocityOne Tuner, so no programmer is
needed and the stock firmware can always be put back.

## What it does

OpenShift runs on the same AIR32F103 board as stock firmware TBRS004-20241224
and started from its behaviour. Later versions will change and improve on it.
Version 0.1.0 keeps:

- the same USB device: descriptors, HID reports, report 0x24 status, LEDs and UART stream
- H-pattern gears 1–8, sequential up/down and analog handbrake mode
- the A/B buttons, the H/SEQ switch and the RGB LEDs
- calibration from VelocityOne Tuner (`24 21 n`), stored in the same settings
  page format, so a calibration made with stock firmware carries over
- gear learning on an uncalibrated unit, using the stock algorithm
- the `24 00` command, which hands over to the vendor bootloader, so Tuner and
  this flasher can update it

It adds things stock doesn't have. A [full list of differences](docs/testing.md#intentional-differences) is in the testing doc.

- **Recovery into the bootloader**, triggered by any of:
  - both buttons held while plugging in
  - any CPU fault
  - a stalled main loop or tick
  - a host that keeps resetting the bus without configuring the device
- A version string (USB iConfiguration, "OpenShift x.y.z") so tools can tell
  which firmware is running.
- Working GET_REPORT and SET_REPORT control requests.

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

## Repository layout

| Path | Contents |
|---|---|
| `firmware/` | firmware sources (C11, freestanding), linker script, Makefile |
| `tools/openshift_flash.py` | Linux flasher (stdlib only; hidraw + sysfs) |
| `tests/test_shifter.py` | host build of the gear/sensor logic, checked against float oracles |
| `tests/test_emulated.py` | whole-firmware tests on an emulated board, incl. differential tests against stock |
| `tests/test_flasher.py` | flasher against a simulated bootloader and a fake sysfs |
| `tests/emu/` | Cortex-M3 + AIR32F103 + MLX90393 + USB host emulator (unicorn) |
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
