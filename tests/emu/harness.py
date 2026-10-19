"""Scenario helpers shared by the emulator tests."""
from __future__ import annotations

import hashlib
import os
import pathlib
import struct

from stm32emu import EmuError, Machine, SETTINGS_ADDR

ROOT = pathlib.Path(__file__).resolve().parents[2]
OPENSHIFT_BIN = ROOT / 'firmware' / 'build' / 'openshift.bin'
STOCK_BIN = pathlib.Path(os.environ.get('OPENSHIFT_STOCK_FW',
                                        ROOT / 'firmware' / 'stock' / 'VelocityOneTuner_Shift_Firmware.bin'))
# Image shipped by VelocityOne Tuner (TBRS004-20241224); used only as a reference.
STOCK_SHA256 = '96da5e6b50f9339b78b99128a069040995fed5e451626a91ffbf7386499dc0d1'


def openshift_image():
    return OPENSHIFT_BIN.read_bytes()


def stock_image():
    """The vendor image, or None when it is not available locally."""
    try:
        data = STOCK_BIN.read_bytes()
    except OSError:
        return None
    if hashlib.sha256(data).hexdigest() != STOCK_SHA256:
        return None
    return data


class Rig:
    """One powered shifter on a simulated USB host."""

    def __init__(self, image, settings=None, *, h_pattern=False, enumerate=True, pos=(0, 0, 0x80)):
        self.image = image
        self.m = Machine(image, settings)
        self.m.buttons['PB10'] = h_pattern
        self.m.host.auto_enumerate = enumerate
        self.m.set_position(*pos)

    # --- time ---
    def run(self, ms):
        self.m.run(ms=ms)

    def boot(self, extra_ms=100):
        self.m.run(until=lambda: self.m.host.state == 'configured', max_ms=5000)
        self.m.run(ms=extra_ms)
        return self

    # --- inputs ---
    def move(self, x, y, z=0x80, ms=150):
        self.m.set_position(x, y, z)
        self.m.run(ms=ms)

    def press(self, pin, ms=100, hold=True):
        self.m.buttons[pin] = hold
        self.m.run(ms=ms)

    def command(self, *payload, ms=60):
        """hid_write of a 64-byte output report on the interrupt OUT endpoint."""
        data = bytes(payload) + bytes(64 - len(payload))
        done = {}

        def g():
            r = yield from self.m.host._token(self.m.usb.out_token, self.m.host.addr, 1, data)
            done['r'] = r
        self.m.host.run_task(g())
        self.m.run(until=lambda: 'r' in done, max_ms=self.m.time_us / 1000 + 1000)
        assert done['r'] == 'ACK', done['r']
        if ms:
            self.m.run(ms=ms)

    def control(self, bm, rq, val, idx, ln, data=None):
        """One control transfer on EP0; returns the IN data, b'' or 'STALL'-like status."""
        res = {}

        def g():
            try:
                res['r'] = yield from self.m.host.control(struct.pack('<BBHHH', bm, rq, val, idx, ln), data)
            except EmuError as e:
                res['r'] = f'ERR {e}'
        self.m.host.run_task(g())
        self.m.run(until=lambda: 'r' in res, max_ms=2000)
        self.m.run(ms=20)
        return res['r']

    def suspend(self, ms=1500):
        """Host stops sending SOFs (bus idle), as a PC does before sleeping."""
        self.m.host.state = 'suspended'
        self.m.usb.suspend()
        self.m.run(ms=ms)

    def resume(self, ms=200):
        self.m.usb.resume()
        self.m.host.state = 'configured'
        self.m.run(ms=ms)

    def bus_resets(self, n, gap_ms=50):
        """A host that keeps resetting the bus and never configures the device."""
        self.m.host.auto_enumerate = False
        for _ in range(n):
            self.m.usb.bus_reset()
            self.m.run(ms=gap_ms)

    def fault(self, exc=3):
        """Pend a fault exception (3 = HardFault) as if the CPU had faulted."""
        self.m.pending.add(exc)

    def reset_events(self):
        return [e[1] for e in self.m.events if e[0] == 'reset']

    # --- outputs ---
    def last(self, report_id):
        for _, r in reversed(self.m.host.in_reports):
            if r[0] == report_id:
                return r
        return None

    def state(self):
        return self.last(0x01), self.last(0x24)

    def settings_page(self, n=2048):
        return self.m.flash_bytes(SETTINGS_ADDR, n)

    def reboot(self, **kw):
        """Power-cycle with the flash contents this unit has written."""
        return Rig(self.image, self.settings_page(), **kw)
