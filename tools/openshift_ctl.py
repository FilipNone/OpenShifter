#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Configure and calibrate a VelocityOne Multi-Shift running OpenShift.

Talks to the shifter through /dev/hidrawN with the same 64-byte output report
(ID 0x24) that VelocityOne Tuner uses, and reads the 0x24 status report back
to confirm each change. Settings are saved on the shifter. Status and
calibration also work with the stock firmware; the LED and button settings
need OpenShift 0.2.0 or newer, white balance and gamma 0.3.0 or newer, the
neutral button 0.5.0 or newer.

Access to /dev/hidraw* needs root or the udev rule printed by
`openshift_flash.py udev-rule`.
"""
import argparse
import os
import re
import select
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openshift_flash import (PID_APP, FlashError, LinuxBus, PACKET,  # noqa: E402
                             REPORT_ID, select_device)

CMD_LED = 0x20          # live colour (Tuner); not saved
CMD_CALIBRATE = 0x21
CMD_COLOR = 0x30
CMD_LOGO = 0x31
CMD_HIGHLOW = 0x32
CMD_BALANCE = 0x33
CMD_GAMMA = 0x34
CMD_NEUTRAL = 0x35
CMD_LED_TEST = 0x3F

OPT_LOGO = 0x01
OPT_RANGE_TOGGLE = 0x02
OPT_RANGE_ON = 0x04
OPT_GAMMA = 0x08
OPT_NEUTRAL = 0x10
OPT_PRESENT = 0x80

DEFAULT_COLOR = (255, 182, 78)     # warm orange, as in the firmware
DEFAULT_GAINS = (255, 162, 96)     # white balance, as in the firmware
MODES = {0: 'H-pattern', 1: 'sequential', 2: 'handbrake'}

# calibration target names -> 24 21 argument
TARGETS = {f'gear{k}': k for k in range(1, 9)}
TARGETS.update({'neutral': 9, 'handbrake': 10, 'seq-up': 3, 'seq-down': 4})


class CtlError(Exception):
    pass


def command(*payload) -> bytes:
    """A 64-byte output report: ID 0x24, payload, zero padding."""
    data = bytes([REPORT_ID, *payload])
    if len(data) > PACKET:
        raise ValueError('command too long')
    return data + bytes(PACKET - len(data))


def s16(b: bytes, i: int) -> int:
    return int.from_bytes(b[i:i + 2], 'big', signed=True)


class Status:
    """The 64-byte 0x24 input report."""

    def __init__(self, raw: bytes):
        if len(raw) != 64 or raw[0] != REPORT_ID:
            raise CtlError('unexpected status report')
        self.raw = raw

    x = property(lambda self: s16(self.raw, 2))
    y = property(lambda self: s16(self.raw, 4))
    color = property(lambda self: tuple(self.raw[6:9]))
    switch_h = property(lambda self: bool(self.raw[18] & 0x04))
    mode = property(lambda self: self.raw[63])
    options = property(lambda self: self.raw[23])
    has_options = property(lambda self: bool(self.raw[23] & OPT_PRESENT))
    logo = property(lambda self: bool(self.raw[23] & OPT_LOGO))
    highlow_toggle = property(lambda self: bool(self.raw[23] & OPT_RANGE_TOGGLE))
    highlow_on = property(lambda self: bool(self.raw[23] & OPT_RANGE_ON))
    gamma = property(lambda self: bool(self.raw[23] & OPT_GAMMA))
    neutral_button = property(lambda self: bool(self.raw[23] & OPT_NEUTRAL))
    gains = property(lambda self: (self.raw[24], self.raw[53], self.raw[54]))

    def gear(self, k):
        i = 13 + 6 * (k - 1)
        return s16(self.raw, i), s16(self.raw, i + 2)

    @property
    def neutral(self):
        return s16(self.raw, 59), s16(self.raw, 9)

    @property
    def handbrake(self):
        return s16(self.raw, 61), s16(self.raw, 11)


class Shifter:
    """An open shifter: write commands, read status reports."""

    def __init__(self, fd, info=None):
        self.fd = fd
        self.info = info
        self.sleep = time.sleep

    def write(self, data: bytes):
        try:
            n = os.write(self.fd, data)
        except OSError as e:
            raise CtlError(f'write failed: {os.strerror(e.errno)}') from None
        if n != len(data):
            raise CtlError(f'short write: {n} of {len(data)} bytes')

    def status(self, timeout=1.0) -> Status:
        """The next 0x24 report the shifter sends (one arrives every 2 ms)."""
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise CtlError('no status report from the shifter')
            r, _, _ = select.select([self.fd], [], [], left)
            if not r:
                continue
            data = os.read(self.fd, 64)
            if len(data) == 64 and data[0] == REPORT_ID:
                return Status(data)

    def fresh_status(self) -> Status:
        """A status report sent after everything already written took effect."""
        self.sleep(0.1)
        self.drain()
        return self.status()

    def drain(self):
        while select.select([self.fd], [], [], 0)[0]:
            os.read(self.fd, 64)

    def close(self):
        os.close(self.fd)


def open_hidraw(path):
    return os.open(path, os.O_RDWR | os.O_CLOEXEC)


def open_shifter(bus, device=None, opener=open_hidraw) -> Shifter:
    info = select_device(bus, device)
    if info.pid != PID_APP:
        raise CtlError(f'{info.path} is in update mode; flash a firmware first')
    try:
        fd = opener(info.path)
    except PermissionError:
        raise CtlError(f'no permission to open {info.path}: run as root or install the udev '
                       f'rule (openshift_flash.py udev-rule)') from None
    return Shifter(fd, info)


def openshift_version(info):
    m = re.match(r'OpenShift (\d+)\.(\d+)\.(\d+)', getattr(info, 'configuration', '') or '')
    return tuple(int(v) for v in m.groups()) if m else None


def need_options(sh: Shifter, version=(0, 2, 0)):
    v = openshift_version(sh.info)
    if v is None or v < version:
        raise CtlError('this setting needs OpenShift {}.{}.{} or newer on the shifter '.format(*version) +
                       f'(found: {getattr(sh.info, "firmware", "unknown")})')


def parse_color(words):
    if len(words) == 1 and words[0] == 'default':
        return DEFAULT_COLOR
    if len(words) == 1:
        m = re.fullmatch(r'#?([0-9a-fA-F]{6})', words[0])
        if not m:
            raise CtlError(f'not a colour: {words[0]} (use R G B, #RRGGBB or default)')
        v = int(m.group(1), 16)
        return v >> 16, (v >> 8) & 0xFF, v & 0xFF
    if len(words) == 3:
        try:
            rgb = tuple(int(w, 0) for w in words)
        except ValueError:
            raise CtlError('R G B must be numbers from 0 to 255') from None
        if all(0 <= c <= 255 for c in rgb):
            return rgb
        raise CtlError('R G B must be numbers from 0 to 255')
    raise CtlError('give R G B, #RRGGBB or default')


def say(msg='', out=None):
    print(msg, file=out or sys.stdout)


# ---------------------------------------------------------------------------
# Commands

def show_status(sh: Shifter, out=None):
    st = sh.status()
    fw = getattr(sh.info, 'firmware', '') if sh.info else ''
    if sh.info:
        say(f'device     {sh.info.path} ({fw})', out)
    say(f'switch     {"H" if st.switch_h else "SEQ"}', out)
    say(f'mode       {MODES.get(st.mode, st.mode)}', out)
    r, g, b = st.color
    say(f'colour     {r} {g} {b} (#{r:02x}{g:02x}{b:02x})', out)
    if st.has_options:
        say(f'logo LED   {"on" if st.logo else "off"}', out)
        hl = 'toggle, ' + ('on' if st.highlow_on else 'off') if st.highlow_toggle else 'button'
        say(f'high/low   {hl}', out)
        if (openshift_version(sh.info) or (0,)) >= (0, 3, 0):
            say('balance    R {} G {} B {}'.format(*st.gains), out)
            say(f'gamma      {"on" if st.gamma else "off"}', out)
        if (openshift_version(sh.info) or (0,)) >= (0, 5, 0):
            say(f'neutral button {"on" if st.neutral_button else "off"}', out)
    say(f'lever      x={st.x} y={st.y}', out)
    say('calibration', out)
    say(f'  neutral    {st.neutral}', out)
    say(f'  handbrake  {st.handbrake}', out)
    for k in range(1, 9):
        say(f'  gear {k}     {st.gear(k)}', out)
    return 0


def set_and_check(sh: Shifter, payload, check, what):
    sh.write(command(*payload))
    st = sh.fresh_status()
    if not check(st):
        raise CtlError(f'the shifter did not apply the {what} change')
    return st


def cmd_logo(sh, args, out=None):
    need_options(sh)
    on = args.state == 'on'
    set_and_check(sh, (CMD_LOGO, int(on)), lambda st: st.logo == on, 'logo LED')
    say(f'logo LED {args.state} (saved)', out)
    return 0


def cmd_color(sh, args, out=None):
    need_options(sh)
    rgb = parse_color(args.rgb)
    set_and_check(sh, (CMD_COLOR, *rgb), lambda st: st.color == rgb, 'colour')
    say('colour {} {} {} (saved)'.format(*rgb), out)
    return 0


def cmd_highlow(sh, args, out=None):
    need_options(sh)
    toggle = args.behaviour == 'toggle'
    set_and_check(sh, (CMD_HIGHLOW, int(toggle)), lambda st: st.highlow_toggle == toggle,
                  'high/low')
    say(f'high/low button: {args.behaviour} (saved)', out)
    return 0


def parse_gains(words):
    if words == ['default']:
        return DEFAULT_GAINS
    try:
        gains = tuple(int(w, 0) for w in words)
    except ValueError:
        gains = ()
    if len(gains) != 3 or not all(0 <= g <= 255 for g in gains):
        raise CtlError('give three gains from 0 to 255 (R G B), or default. To adjust by\n'
                       'eye, run "balance" alone and type changes like "b -10" at its prompt')
    return gains


def cmd_gamma(sh, args, out=None):
    need_options(sh, (0, 3, 0))
    on = args.state == 'on'
    set_and_check(sh, (CMD_GAMMA, int(on)), lambda st: st.gamma == on, 'gamma')
    say(f'gamma correction {args.state} (saved)', out)
    return 0


def cmd_neutral(sh, args, out=None):
    need_options(sh, (0, 5, 0))
    on = args.state == 'on'
    set_and_check(sh, (CMD_NEUTRAL, int(on)), lambda st: st.neutral_button == on, 'neutral button')
    say(f'neutral button {args.state} (saved)', out)
    return 0


def set_gains(sh, gains, save):
    sh.write(command(CMD_BALANCE, *gains, int(save)))


def cmd_balance(sh, args, out=None, inp=None):
    need_options(sh, (0, 3, 0))
    if args.gains:
        gains = parse_gains(args.gains)
        set_and_check(sh, (CMD_BALANCE, *gains, 1), lambda st: st.gains == gains, 'balance')
        say('balance R {} G {} B {} (saved)'.format(*gains), out)
        return 0
    return balance_guided(sh, inp or sys.stdin, out or sys.stdout)


def balance_guided(sh, inp, out):
    """Show white on the lit LEDs and let the user trim each channel by eye."""
    st = sh.fresh_status()
    color, start = st.color, list(st.gains)
    gains, saved = list(start), False
    say('The lit LEDs now show white (255 255 255). Turn down the channel that\n'
        'stands out until they look white: "b -20" is less blue, "r +10" more red,\n'
        '"g 150" sets green to 150. An empty line saves, "q" cancels.', out)
    sh.write(command(CMD_LED, 0, 0, 0, 0, 255, 255, 255))
    try:
        while True:
            line = ask('R {} G {} B {} > '.format(*gains), inp, out).lower()
            if not line:
                set_gains(sh, gains, True)
                saved = True
                say('balance R {} G {} B {} (saved)'.format(*gains), out)
                return 0
            if line in ('q', 'quit'):
                say('cancelled; balance unchanged', out)
                return 0
            m = re.fullmatch(r'([rgb])\s*([+-]?)\s*(\d+)', line)
            if not m:
                say('type a channel and a value, for example "b -20" or "g 150"', out)
                continue
            i, sign, v = 'rgb'.index(m.group(1)), m.group(2), int(m.group(3))
            v = gains[i] + v if sign == '+' else gains[i] - v if sign == '-' else v
            gains[i] = max(0, min(255, v))
            set_gains(sh, gains, False)
    finally:
        if not saved:
            set_gains(sh, start, False)
        sh.write(command(CMD_LED, 0, 0, 0, 0, *color))


def ask(prompt, inp, out):
    if out:
        out.write(prompt)
        out.flush()
    line = inp.readline()
    if not line:
        raise CtlError('cancelled')
    return line.strip()


def calibrate_one(sh, name, out=None):
    st = sh.fresh_status()
    target = TARGETS[name]
    if name.startswith('seq-'):
        if st.mode != 1:
            raise CtlError('put the switch in SEQ, with handbrake mode off, to calibrate '
                           'the sequential thresholds')
    elif name.startswith('gear') and st.mode != 0:
        raise CtlError('put the switch in H to calibrate the gears')
    sh.write(command(CMD_CALIBRATE, target))
    st = sh.fresh_status()
    if name.startswith('gear'):
        say(f'{name}: {st.gear(target)}', out)
    elif name == 'neutral':
        say(f'neutral: {st.neutral}', out)
    elif name == 'handbrake':
        say(f'handbrake: {st.handbrake}', out)
    elif name == 'seq-up':
        say(f'seq-up: threshold y={max(st.y, 900)}', out)
    else:
        say(f'seq-down: threshold y={min(st.y, -900)}', out)


def calibrate_guided(sh, inp, out, sequential):
    # Gears go last. Until the unit restarts with a full calibration, stock
    # gear learning runs and can store any steady pose (handbrake, seq up and
    # down) as a gear with a smaller radius; calibrating a gear overwrites that.
    for name, text in (('neutral', 'Leave the lever in neutral'),
                       ('handbrake', 'Pull the lever fully back, as a handbrake, and hold it')):
        ask(f'{text}, then press Enter. ', inp, out)
        calibrate_one(sh, name, out)
    if sequential:
        while sh.fresh_status().mode != 1:
            ask('Put the switch in SEQ with handbrake mode off, then press Enter. ', inp, out)
        for name, text in (('seq-up', 'Push the lever forward (shift up) and hold it'),
                           ('seq-down', 'Pull the lever back (shift down) and hold it')):
            ask(f'{text}, then press Enter. ', inp, out)
            calibrate_one(sh, name, out)
    while not sh.fresh_status().switch_h:
        ask('Put the H/SEQ switch in H, then press Enter. ', inp, out)
    for k in range(1, 9):
        ask(f'Put the lever in gear {k} and hold it, then press Enter. ', inp, out)
        calibrate_one(sh, f'gear{k}', out)
    say('Calibration saved. Unplug the shifter and plug it back in: until it '
        'restarts, the old gear learning keeps adjusting the gears.', out)


def cmd_calibrate(sh, args, out=None, inp=None):
    if args.target:
        calibrate_one(sh, args.target, out)
        say('Saved. It takes full effect after the shifter restarts (unplug and plug it in).', out)
    else:
        calibrate_guided(sh, inp or sys.stdin, out or sys.stdout, args.sequential)
    return 0


def cmd_identify(sh, args, out=None, inp=None):
    """Light each LED position alone so the user can say which one it is."""
    need_options(sh)
    inp, out = inp or sys.stdin, out or sys.stdout
    found = {}
    try:
        for i in range(3):
            sh.write(command(CMD_LED_TEST, i))
            found[i] = ask(f'LED position {i} is white. Which LED is it '
                           '(logo / handbrake / highlow / none)? ', inp, out)
    finally:
        sh.write(command(CMD_LED_TEST, 0xFF))
    say('', out)
    for i, what in found.items():
        say(f'position {i}: {what}', out)
    return 0


def main(argv=None, bus=None, inp=None, out=None, opener=open_hidraw):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('--device', help='/dev/hidrawN when several shifters are connected')
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('status', help='show the settings, mode, lever and calibration')
    s = sub.add_parser('logo', help='turn the logo LED on or off')
    s.add_argument('state', choices=['on', 'off'])
    s = sub.add_parser('color', help='set the LED colour: R G B, #RRGGBB or default (warm orange)')
    s.add_argument('rgb', nargs='+')
    s = sub.add_parser('highlow', help='make the high/low button a button or a toggle')
    s.add_argument('behaviour', choices=['button', 'toggle'])
    s = sub.add_parser('calibrate', help='guided calibration, or one target')
    s.add_argument('target', nargs='?', choices=sorted(TARGETS, key=lambda t: (TARGETS[t], t)),
                   help='calibrate only this target, at the current lever position')
    s.add_argument('--sequential', action='store_true',
                   help='also calibrate the sequential up/down thresholds')
    s = sub.add_parser('balance', help='LED white balance: guided, R G B gains, or default')
    s.add_argument('gains', nargs='*')
    s = sub.add_parser('gamma', help='turn LED gamma correction on or off (off by default)')
    s.add_argument('state', choices=['on', 'off'])
    s = sub.add_parser('neutral', help='hold button 1 while the lever is in neutral, '
                       'for games that need a neutral binding (off by default)')
    s.add_argument('state', choices=['on', 'off'])
    sub.add_parser('identify-leds', help='light each LED in turn to find which is which')
    args = p.parse_args(argv)

    handlers = {'status': lambda sh: show_status(sh, out),
                'logo': lambda sh: cmd_logo(sh, args, out),
                'color': lambda sh: cmd_color(sh, args, out),
                'highlow': lambda sh: cmd_highlow(sh, args, out),
                'calibrate': lambda sh: cmd_calibrate(sh, args, out, inp),
                'balance': lambda sh: cmd_balance(sh, args, out, inp),
                'gamma': lambda sh: cmd_gamma(sh, args, out),
                'neutral': lambda sh: cmd_neutral(sh, args, out),
                'identify-leds': lambda sh: cmd_identify(sh, args, out, inp)}
    try:
        sh = open_shifter(bus or LinuxBus(), args.device, opener)
        try:
            return handlers[args.cmd](sh)
        finally:
            sh.close()
    except (CtlError, FlashError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('\ninterrupted', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
