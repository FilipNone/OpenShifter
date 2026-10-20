# OpenShifter

Free replacement firmware for the **Turtle Beach VelocityOne Multi-Shift**, installed from your browser.

**[Open the web tool](https://filipnone.github.io/OpenShifter/)** · [User guide](https://github.com/FilipNone/OpenShifter/wiki) · [Download](https://github.com/FilipNone/OpenShifter/releases/latest)

![The web tool with a shifter connected](wiki/images/settings.png)

## What you get

- Any LED colour, and the logo LED on or off
- The Handbrake button as a normal button while the switch is in H
- The High/low button as a toggle, if you like
- A neutral button, for games that stay in gear when you move the lever to neutral
- On Linux, games find the shifter as a joystick with no extra driver
- The Turtle Beach firmware can be put back at any time

## Install

You need any Chromium based browser (Chrome, Edge, Opera...) on a computer.

1. Open the [web tool](https://filipnone.github.io/OpenShifter/) and connect the shifter.
2. Install OpenShift from the **Firmware** tab. This erases your calibration.
3. Calibrate in the **Calibration** tab.

Step by step: [Install](https://github.com/FilipNone/OpenShifter/wiki/Install) · From a terminal: [Command line](https://github.com/FilipNone/OpenShifter/wiki/Command-line) · Problems: [Troubleshooting](https://github.com/FilipNone/OpenShifter/wiki/Troubleshooting)

<details>
<summary>Using Firefox or a browser built on it (Zen, LibreWolf...)</summary>

Firefox cannot reach USB devices by itself. The third-party add-on [WebHID for Firefox](https://github.com/Sainan/WebHID-for-Firefox) adds this, with a helper program that runs on your computer. Setup: [Firefox](https://github.com/FilipNone/OpenShifter/wiki/Firefox).

- It is not tested with OpenShift. Install firmware with a Chromium based browser.
- On Linux and macOS, any website can use the helper while it runs. Start it only while you use the web tool.

</details>

## Development

```sh
make -C firmware          # firmware/build/openshift.bin (needs clang and lld 15+)
make -C firmware test     # all tests (pip install -r requirements-dev.txt first)
cd web && npm ci && npm run dev   # web tool on http://localhost:5173/
```

| Folder | Contents |
|---|---|
| `firmware/` | the firmware, in C |
| `web/` | the web tool, published to GitHub Pages |
| `tools/` | Linux command-line flasher and settings tool |
| `tests/` | tests, including an emulator of the shifter |
| `wiki/` | the user guide, copied to the GitHub wiki |
| `docs/` | [USB protocol](docs/protocol.md) and [what is tested](docs/testing.md) |

## License

MIT, see [LICENSE](LICENSE). OpenShift is not made or endorsed by Turtle Beach.
