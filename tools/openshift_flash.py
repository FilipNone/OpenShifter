#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Flash firmware to a Turtle Beach VelocityOne Multi-Shift from Linux.

Speaks the update protocol of the shifter's vendor bootloader, as used by
VelocityOne Tuner (recovered from Tuner 1.1.13.0):

  application 10f5:7096   write 24 00             -> reboots into the bootloader
  bootloader  10f5:7097   write 24 01             erase the application area,
                                                  then wait 2 s
                          write 24 02 iL iH d*60  chunk i (image[60*i:60*i+60]),
                                                  15 ms apart
                          write 24 03             done, start the application

Every write is a 64-byte output report (report ID 0x24 first; the unused tail
is 0xFF for chunks and 0x00 for commands), exactly what Tuner sends. Images
are raw binaries linked at 0x08010000 with no header or checksum. The
bootloader itself (0x08000000-0x0800EFFF) is never written, so the stock
firmware can be restored with this tool the same way. An update erases the
settings page, which holds the calibration.

Only the Python standard library is needed. Access to /dev/hidraw* needs root
or the udev rule printed by `openshift_flash.py udev-rule`.
"""
from __future__ import annotations

import argparse
import errno
import hashlib
import os
import re
import struct
import sys
import time

VID = 0x10F5
PID_APP = 0x7096
PID_BOOT = 0x7097

REPORT_ID = 0x24
CMD_BOOT, CMD_ERASE, CMD_WRITE, CMD_DONE = 0x00, 0x01, 0x02, 0x03
PACKET = 64
CHUNK = 60
ERASE_WAIT_S = 2.0
CHUNK_GAP_S = 0.015

APP_BASE = 0x08010000
APP_MAX = 0x10000            # 0x08010000-0x0801FFFF on the 128 KB part
RAM_BASE, RAM_END = 0x20000000, 0x20010000

# Images that are known to work on the unit.
KNOWN_STOCK = {
    '96da5e6b50f9339b78b99128a069040995fed5e451626a91ffbf7386499dc0d1':
        'Turtle Beach stock firmware TBRS004-20241224 (VelocityOne Tuner 1.1.13.0)',
}
OPENSHIFT_MARK = re.compile(rb'OpenShift (\d+\.\d+\.\d+[0-9A-Za-z.+-]*)')

UDEV_RULE = """\
# /etc/udev/rules.d/70-openshift.rules
# VelocityOne Multi-Shift: application (7096) and vendor bootloader (7097).
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="10f5", ATTRS{idProduct}=="7096", TAG+="uaccess"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="10f5", ATTRS{idProduct}=="7097", TAG+="uaccess"
"""


class FlashError(Exception):
    pass


# ---------------------------------------------------------------------------
# Protocol

def command(cmd: int) -> bytes:
    return bytes([REPORT_ID, cmd]) + bytes(PACKET - 2)


def chunk_count(n: int) -> int:
    return (n + CHUNK - 1) // CHUNK


def chunk(image: bytes, i: int) -> bytes:
    """Tuner fills a 64-byte buffer with 0xFF, then writes 24 02, int16 i, data."""
    buf = bytearray(b'\xff' * PACKET)
    buf[0:4] = struct.pack('<BBh', REPORT_ID, CMD_WRITE, i)
    data = image[i * CHUNK:(i + 1) * CHUNK]
    buf[4:4 + len(data)] = data
    return bytes(buf)


class Image:
    def __init__(self, data: bytes, name: str = '<image>'):
        self.data = bytes(data)
        self.name = name
        self.sha256 = hashlib.sha256(self.data).hexdigest()
        m = OPENSHIFT_MARK.search(self.data)
        self.openshift_version = m.group(1).decode() if m else None
        self.stock = KNOWN_STOCK.get(self.sha256)

    @property
    def kind(self) -> str:
        if self.openshift_version:
            return f'OpenShift {self.openshift_version}'
        if self.stock:
            return self.stock
        return 'unrecognised image'

    @property
    def known(self) -> bool:
        return bool(self.openshift_version or self.stock)

    def problems(self) -> list[str]:
        """Reasons this cannot be an application image for the shifter."""
        d, out = self.data, []
        if len(d) < 64:
            return [f'{len(d)} bytes is too small for a Cortex-M3 vector table']
        if len(d) > APP_MAX:
            out.append(f'{len(d)} bytes does not fit the {APP_MAX} byte application area')
        sp, reset, nmi, hard = struct.unpack_from('<4I', d)
        if not (RAM_BASE < sp <= RAM_END) or sp & 3:
            out.append(f'initial stack pointer {sp:#010x} is not in RAM')
        end = APP_BASE + len(d)
        for name, v in (('reset', reset), ('NMI', nmi), ('HardFault', hard)):
            if name != 'reset' and v == 0:
                continue
            if not (v & 1) or not (APP_BASE <= (v & ~1) < end):
                out.append(f'{name} vector {v:#010x} is not a Thumb address inside an image '
                           f'linked at {APP_BASE:#010x} (wrong link address or not a raw .bin?)')
        return out


# ---------------------------------------------------------------------------
# Devices

class DeviceInfo:
    def __init__(self, path, pid, name='', configuration='', serial='', report_descriptor=b''):
        self.path = path
        self.pid = pid
        self.name = name
        self.configuration = configuration
        self.serial = serial
        self.report_descriptor = report_descriptor

    @property
    def mode(self) -> str:
        return {PID_APP: 'application', PID_BOOT: 'bootloader'}.get(self.pid, f'pid {self.pid:04x}')

    @property
    def firmware(self) -> str:
        if self.pid == PID_BOOT:
            return 'bootloader (update mode)'
        if self.configuration.startswith('OpenShift'):
            return self.configuration
        return 'stock (no OpenShift identification)'

    def has_output_report(self, report_id=REPORT_ID) -> bool | None:
        """True/False from the report descriptor, None if it is unavailable."""
        d = self.report_descriptor
        if not d:
            return None
        rid, i = 0, 0
        while i < len(d):
            b = d[i]
            if b == 0xFE:                      # long item
                if i + 1 >= len(d):
                    break
                i += 3 + d[i + 1]
                continue
            size = (0, 1, 2, 4)[b & 3]
            val = int.from_bytes(d[i + 1:i + 1 + size], 'little')
            tag = b & 0xFC
            if tag == 0x84:
                rid = val
            elif tag == 0x90 and rid == report_id:
                return True
            i += 1 + size
        return False


def _read(path, binary=False):
    try:
        with open(path, 'rb') as f:
            data = f.read()
    except OSError:
        return b'' if binary else ''
    return data if binary else data.decode(errors='replace').strip()


class LinuxBus:
    """Finds the shifter through sysfs and talks to it through /dev/hidrawN."""

    def __init__(self, sysfs='/sys/class/hidraw', dev='/dev'):
        self.sysfs = sysfs
        self.dev = dev
        self.sleep = time.sleep
        self.monotonic = time.monotonic

    def devices(self) -> list[DeviceInfo]:
        out = []
        try:
            names = sorted(os.listdir(self.sysfs))
        except OSError:
            return out
        for name in names:
            hid = os.path.join(self.sysfs, name, 'device')
            m = re.search(r'^HID_ID=([0-9A-Fa-f]+):([0-9A-Fa-f]+):([0-9A-Fa-f]+)$',
                          _read(os.path.join(hid, 'uevent')), re.M)
            if not m or int(m.group(2), 16) != VID:
                continue
            pid = int(m.group(3), 16)
            if pid not in (PID_APP, PID_BOOT):
                continue
            usb = os.path.realpath(hid)
            while usb != '/' and not os.path.exists(os.path.join(usb, 'idVendor')):
                usb = os.path.dirname(usb)
            name_m = re.search(r'^HID_NAME=(.*)$', _read(os.path.join(hid, 'uevent')), re.M)
            out.append(DeviceInfo(
                os.path.join(self.dev, name), pid,
                name=name_m.group(1) if name_m else '',
                configuration=_read(os.path.join(usb, 'configuration')) if usb != '/' else '',
                serial=_read(os.path.join(usb, 'serial')) if usb != '/' else '',
                report_descriptor=_read(os.path.join(hid, 'report_descriptor'), binary=True)))
        return out

    def open(self, info: DeviceInfo):
        deadline = self.monotonic() + 3.0
        while True:   # udev may still be applying permissions to a new node
            try:
                return HidrawHandle(os.open(info.path, os.O_RDWR | os.O_CLOEXEC))
            except PermissionError:
                if self.monotonic() > deadline:
                    raise FlashError(f'no permission to open {info.path}: run as root or install '
                                     f'the udev rule (openshift_flash.py udev-rule)') from None
            except FileNotFoundError:
                if self.monotonic() > deadline:
                    raise FlashError(f'{info.path} disappeared') from None
            self.sleep(0.1)


class HidrawHandle:
    def __init__(self, fd):
        self.fd = fd

    def write(self, data: bytes):
        try:
            n = os.write(self.fd, data)
        except OSError as e:
            raise FlashError(f'write failed: {os.strerror(e.errno)}') from None
        if n != len(data):
            raise FlashError(f'short write: {n} of {len(data)} bytes')

    def close(self):
        try:
            os.close(self.fd)
        except OSError as e:
            if e.errno != errno.EBADF:
                raise


# ---------------------------------------------------------------------------
# Simulated shifter, for --dry-run and the tests

class SimShifter:
    """A shifter whose bootloader behaves as Tuner expects, on a virtual clock.

    It enforces the timing Tuner uses (2 s after erase, 15 ms between chunks)
    and keeps the application area so the result can be checked.
    """
    REENUMERATE_S = 1.0

    def __init__(self, firmware: bytes | None = None, *, boot_mode=False):
        self.now = 0.0
        self.flash = bytearray(b'\xff' * APP_MAX)
        if firmware:
            self.flash[:len(firmware)] = firmware
        self.pid = PID_BOOT if boot_mode else PID_APP
        self.gone_until = 0.0
        self.log = []
        self.erased_at = None
        self.last_chunk = None
        self.next_index = 0
        self.violations = []

    # bus interface
    def sleep(self, s):
        self.now += s

    def monotonic(self):
        return self.now

    def devices(self):
        if self.now < self.gone_until:
            return []
        cfg = ''
        if self.pid == PID_APP:
            m = OPENSHIFT_MARK.search(bytes(self.flash))
            cfg = f'OpenShift {m.group(1).decode()}' if m else ''
        rd = bytes.fromhex('0600ff0901a1018524090115002600ff7508953f9102c0')
        return [DeviceInfo('/dev/sim', self.pid, 'VelocityOne Multi-Shift', cfg, '', rd)]

    def open(self, info):
        if not self.devices() or info.pid != self.pid:
            raise FlashError('simulated device is not present')
        return self

    def close(self):
        pass

    def write(self, data: bytes):
        if self.now < self.gone_until or not data:
            raise FlashError('write failed: device not present')
        if len(data) != PACKET or data[0] != REPORT_ID:
            self.violations.append(('framing', data.hex()))
        cmd = data[1]
        self.log.append((round(self.now, 4), cmd))
        if self.pid == PID_APP:
            if cmd == CMD_BOOT:
                self._reenumerate(PID_BOOT)
            return
        if cmd == CMD_ERASE:
            self.flash[:] = b'\xff' * APP_MAX
            self.erased_at, self.next_index, self.last_chunk = self.now, 0, None
        elif cmd == CMD_WRITE:
            i = struct.unpack_from('<h', data, 2)[0]
            if self.erased_at is None:
                self.violations.append(('write before erase', i))
            elif self.now - self.erased_at < ERASE_WAIT_S - 1e-9:
                self.violations.append(('write during erase', i))
            if self.last_chunk is not None and self.now - self.last_chunk < CHUNK_GAP_S - 1e-9:
                self.violations.append(('chunks too fast', i))
            if i != self.next_index:
                self.violations.append(('out of order', i, self.next_index))
            self.flash[i * CHUNK:(i + 1) * CHUNK] = data[4:]
            self.last_chunk, self.next_index = self.now, i + 1
        elif cmd == CMD_DONE:
            self._reenumerate(PID_APP)

    def _reenumerate(self, pid):
        self.pid = pid
        self.gone_until = self.now + self.REENUMERATE_S

    def application(self, n):
        return bytes(self.flash[:n])


# ---------------------------------------------------------------------------
# Flashing

def say(msg, out=None):
    print(msg, file=out or sys.stdout, flush=True)


def wait_for(bus, pid, timeout_s):
    deadline = bus.monotonic() + timeout_s
    while bus.monotonic() < deadline:
        found = [d for d in bus.devices() if d.pid == pid]
        if found:
            return found[0]
        bus.sleep(0.1)
    raise FlashError(f'timed out waiting for 10f5:{pid:04x}')


def select_device(bus, path=None):
    devs = bus.devices()
    if path:
        devs = [d for d in devs if d.path == path]
    if not devs:
        raise FlashError('no VelocityOne Multi-Shift found (10f5:7096 or 10f5:7097)')
    if len(devs) > 1:
        raise FlashError('several shifters found; pick one with --device: ' +
                         ', '.join(d.path for d in devs))
    return devs[0]


def flash(bus, image: Image, *, device=None, out=None, progress=True):
    """Runs the whole update. Returns the DeviceInfo of the restarted shifter."""
    dev = select_device(bus, device)
    if dev.pid == PID_APP:
        say(f'{dev.path}: {dev.firmware}; switching to update mode (24 00)', out)
        h = bus.open(dev)
        try:
            h.write(command(CMD_BOOT))
        finally:
            h.close()
        bus.sleep(0.5)
        dev = wait_for(bus, PID_BOOT, 10.0)
    say(f'{dev.path}: bootloader 10f5:7097', out)
    if dev.has_output_report() is False:
        raise FlashError('the bootloader does not declare output report 0x24; '
                         'refusing to erase (nothing has been changed)')

    n = chunk_count(len(image.data))
    h = bus.open(dev)
    try:
        say('erasing (24 01)', out)
        h.write(command(CMD_ERASE))
        bus.sleep(ERASE_WAIT_S)
        t_next = bus.monotonic()
        for i in range(n):
            delay = t_next - bus.monotonic()
            if delay > 0:
                bus.sleep(delay)
            h.write(chunk(image.data, i))
            t_next = bus.monotonic() + CHUNK_GAP_S
            if progress and (i % 50 == 0 or i == n - 1):
                say(f'  chunk {i + 1}/{n}', out)
        bus.sleep(CHUNK_GAP_S)
        say('finishing (24 03)', out)
        h.write(command(CMD_DONE))
    except FlashError as e:
        raise FlashError(f'{e}\nThe update was interrupted. The shifter should still be in update '
                         f'mode (10f5:7097): run the same command again to retry.') from None
    finally:
        h.close()

    bus.sleep(0.5)
    app = wait_for(bus, PID_APP, 15.0)
    say(f'{app.path}: restarted as 10f5:7096, firmware: {app.firmware}', out)
    if image.openshift_version and app.configuration != f'OpenShift {image.openshift_version}':
        raise FlashError(f'the shifter restarted but reports {app.configuration!r} instead of '
                         f'OpenShift {image.openshift_version}')
    return app


# ---------------------------------------------------------------------------
# Command line

def load_image(path, allow_foreign):
    if path.endswith(('.elf', '.hex')):
        raise FlashError(f'{path}: give the raw .bin image, not {path.rsplit(".", 1)[1]}')
    try:
        with open(path, 'rb') as f:
            img = Image(f.read(), path)
    except OSError as e:
        raise FlashError(f'{path}: {e.strerror}') from None
    probs = img.problems()
    if probs:
        raise FlashError(f'{path} is not a Multi-Shift application image:\n  ' + '\n  '.join(probs))
    if not img.known and not allow_foreign:
        raise FlashError(f'{path} is neither an OpenShift build nor a known stock image; '
                         f'pass --allow-foreign if you are sure it is for this shifter')
    return img


def describe(img: Image, out=None):
    say(f'image   {img.name}', out)
    say(f'kind    {img.kind}', out)
    say(f'size    {len(img.data)} bytes, {chunk_count(len(img.data))} chunks of {CHUNK}', out)
    say(f'sha256  {img.sha256}', out)


def cmd_info(args, bus):
    devs = bus.devices()
    if not devs:
        say('no VelocityOne Multi-Shift found')
        return 1
    for d in devs:
        say(f'{d.path}  10f5:{d.pid:04x}  {d.mode:11s}  firmware: {d.firmware}')
    return 0


def cmd_check(args, bus):
    describe(load_image(args.image, args.allow_foreign))
    say('ok')
    return 0


def cmd_flash(args, bus):
    img = load_image(args.image, args.allow_foreign)
    describe(img)
    if args.dry_run:
        sim = SimShifter()
        say('dry run: simulated shifter, nothing is sent to hardware')
        flash(sim, img, progress=False)
        if sim.violations:
            raise FlashError(f'protocol violations: {sim.violations[:5]}')
        if sim.application(len(img.data)) != img.data:
            raise FlashError('simulated flash content differs from the image')
        say(f'dry run ok: {len(sim.log)} writes, {sim.now:.1f} s')
        return 0
    dev = select_device(bus, args.device)
    say(f'device  {dev.path} ({dev.mode}, firmware: {dev.firmware})')
    if not args.yes:
        say('\nThis replaces the shifter firmware. Keep it plugged in until it restarts.\n'
            'The update also erases the calibration; recalibrate afterwards.\n'
            'The vendor bootloader is not touched; the stock image can be restored the same way.')
        try:
            answer = input('Type "flash" to continue: ')
        except EOFError:
            answer = ''
        if answer.strip() != 'flash':
            say('cancelled; nothing was changed')
            return 1
    flash(bus, img, device=dev.path)
    say('done')
    return 0


def cmd_udev(args, bus):
    sys.stdout.write(UDEV_RULE)
    return 0


def main(argv=None, bus=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    sub.add_parser('info', help='list connected shifters (read-only)').set_defaults(fn=cmd_info)
    c = sub.add_parser('check', help='validate an image without touching any device')
    c.add_argument('image')
    c.add_argument('--allow-foreign', action='store_true')
    c.set_defaults(fn=cmd_check)
    f = sub.add_parser('flash', help='write an image to the shifter')
    f.add_argument('image')
    f.add_argument('--device', help='/dev/hidrawN when several shifters are connected')
    f.add_argument('--allow-foreign', action='store_true',
                   help='accept an image that is neither OpenShift nor a known stock build')
    f.add_argument('--dry-run', action='store_true',
                   help='run the full update against a simulated shifter')
    f.add_argument('-y', '--yes', action='store_true', help='do not ask for confirmation')
    f.set_defaults(fn=cmd_flash)
    sub.add_parser('udev-rule', help='print a udev rule granting access to the shifter') \
        .set_defaults(fn=cmd_udev)
    args = p.parse_args(argv)
    try:
        return args.fn(args, bus or LinuxBus())
    except FlashError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('\ninterrupted', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
