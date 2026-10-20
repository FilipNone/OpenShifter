## Modes

The **H/SEQ switch** picks the mode. In SEQ, the **Handbrake button** switches between sequential and handbrake mode.

| Switch | Mode | The lever | Handbrake button LED |
|---|---|---|---|
| H | H-pattern | gears 1 to 7 and R. Push it down to reach 7 and R. | your colour |
| SEQ | Sequential | pull back to shift up, push forward to shift down | off |
| SEQ, then press the Handbrake button | Handbrake | pull back to brake. Press the button again to go back to sequential. | green |

## What games see

The shifter shows up as a joystick named **VelocityOne Multi-Shift**. Map these in the game's controller settings.

Linux and Windows count buttons differently, so look at the column for your system:

| Linux button | Windows button | Comes from |
|---|---|---|
| 0 | 1 | lever in neutral, with the switch in H. Only if the [neutral button](Settings) is on. |
| 1 to 7 | 2 to 8 | gears 1 to 7 |
| 8 | 9 | reverse |
| 9 | 10 | lever pulled back in sequential mode (shift up) |
| 10 | 11 | lever pushed forward in sequential mode (shift down) |
| 11 | 12 | High/low button |
| 12 | 13 | Handbrake button, with the switch in H |
| Z axis | Z axis | the lever in handbrake mode |

With the switch in SEQ, the Handbrake button changes mode and games do not see it.

This numbering is new in OpenShift 0.4.1. With 0.4.0, every button is one lower, so redo your game's mappings after updating.

### Neutral

Most driving games go to neutral by themselves when you move the lever out of gear: set the game's gearbox to **H-shifter** (or "H-pattern", "manual with clutch").

Some games keep the last gear until you pick another one. For those, turn on the **Neutral button** in [Settings](Settings) and bind the first button (Linux 0, Windows 1) to the game's **Neutral** action.

## LEDs

| LED | Shows |
|---|---|
| Handbrake button | see the mode table above |
| High/low button | your colour. As a toggle: red for off, green for on. |
| Logo | your colour, if turned on in [Settings](Settings) |
