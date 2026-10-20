On Linux, two Python scripts do everything the web tool does. They need Python 3.9 or newer and nothing else.

```sh
git clone https://github.com/FilipNone/OpenShifter.git
cd OpenShifter
```

## Access without root (once)

```sh
python3 tools/openshift_flash.py udev-rule | sudo tee /etc/udev/rules.d/70-openshift.rules
sudo udevadm control --reload-rules
```

Then unplug the shifter and plug it back in. Without the rule, run the commands below with `sudo`.

## Install firmware

Download the `openshift-….bin` file from the [latest release](https://github.com/FilipNone/OpenShifter/releases/latest) and use its name in the command below. Installing **erases your calibration and settings**.

```sh
python3 tools/openshift_flash.py info                        # which firmware is running (changes nothing)
python3 tools/openshift_flash.py flash openshift-0.4.1.bin   # asks you to type "flash" first
```

The same `flash` command puts the stock firmware back: give it `VelocityOneTuner_Shift_Firmware.bin` instead. If a flash stops halfway, run the same command again.

## Calibrate and change settings

```sh
python3 tools/openshift_ctl.py calibrate                # guided: neutral, handbrake, gears 1 to 8
python3 tools/openshift_ctl.py calibrate --sequential   # also the sequential thresholds
python3 tools/openshift_ctl.py status                   # settings, mode, lever position
python3 tools/openshift_ctl.py color 255 182 78         # or '#ff8000', or default
python3 tools/openshift_ctl.py logo on                  # or off
python3 tools/openshift_ctl.py highlow toggle           # or button
python3 tools/openshift_ctl.py balance                  # white balance, guided
python3 tools/openshift_ctl.py gamma on                 # or off
python3 tools/openshift_ctl.py neutral on               # neutral button, or off
```

After calibrating, unplug the shifter and plug it back in. `calibrate` and `status` also work with the stock firmware.
