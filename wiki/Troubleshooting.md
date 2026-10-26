| Problem | What to do |
|---|---|
| The page says the browser cannot reach USB devices | Open the page in Chrome, Edge or Opera on a computer. For Firefox, see [Firefox](Firefox). |
| The shifter is missing from the browser's list (Linux) | Install the udev rule from [Install](Install#before-you-start), then unplug the shifter and plug it back in. |
| "Could not connect" or "could not open the shifter" | Close VelocityOne Tuner and any other program that uses the shifter, then try again. On Linux, check the udev rule. |
| The update stopped halfway | The shifter waits in update mode. Connect it again in the web tool, open the **Firmware** tab and install again. |
| An update started in Firefox stopped | Firmware updates do not work in Firefox. The shifter waits in update mode: open the web tool in Chrome, Edge or Opera, connect it and install again from the **Firmware** tab. |
| Firefox shows an error when you change a setting | This is normal with the [Firefox add-on](Firefox). The setting still changes. |
| The shifter does nothing after an update | Start it in update mode by hand (below), then install again. |
| A gear is detected wrong | [Calibrate](Calibrate) again, then unplug the shifter and plug it back in. |
| No gear shows right after plugging in | Move the lever through neutral once. |
| The Settings tab says it needs OpenShifter | The shifter runs the stock firmware. [Install OpenShifter](Install) first. |
| Linux: games see two shifters, or every press twice | You have the `hid-velocityone-multi-shift` driver installed. OpenShifter does not need it: stop and remove it. |

## Start in update mode by hand

Use this if OpenShifter is installed but the shifter does not work.

1. Unplug the shifter.
2. Hold down **both** buttons on the shifter, **High/low** and **Handbrake**.
3. Plug it back in while you hold them, then let go.
4. Connect it in the **[web tool](https://filipnone.github.io/OpenShifter/)**. Only the Firmware tab is shown. Install any firmware.

OpenShifter also starts update mode by itself if it crashes or hangs.

Still stuck? [Open an issue](https://github.com/FilipNone/OpenShifter/issues).
