"""Tests of tools/openshift_ctl.py against the real shifter core.

A thread plays the shifter: it runs firmware/src/shifter.c (built by
test_shifter.py) with a simulated lever, buttons and switch, sends 0x24
reports and runs the commands the tool writes. The tool talks to it through a
SEQPACKET socket, which keeps report boundaries like /dev/hidraw does.
"""
import ctypes
import io
import os
import pathlib
import socket
import sys
import threading
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import openshift_ctl as ctl  # noqa: E402
import openshift_flash as fl  # noqa: E402
import test_shifter as ts  # noqa: E402


def setUpModule():
    ts.setUpModule()


def tearDownModule():
    ts.tearDownModule()


class Device(threading.Thread):
    """The shifter side of the socket."""

    def __init__(self, sock):
        super().__init__(daemon=True)
        self.sock = sock
        sock.setblocking(False)
        self.s = ctypes.create_string_buffer(ts.lib.t_sizeof())
        ts.lib.shifter_init(self.s)
        ts.lib.shifter_load(self.s, (ctypes.c_uint16 * 100)(*[0xFFFF] * 100))
        self.pose = (0, 0, 0x80)
        self.switch_h, self.btn_a, self.btn_b = 1, 0, 0
        self.ticks = 0
        self.running = True
        self.lock = threading.Lock()
        self.received = []

    def run(self):
        lib = ts.lib
        while self.running:
            try:
                data = self.sock.recv(64)
                self.received.append(data)
                with self.lock:
                    lib.shifter_command(self.s, data, len(data))
            except BlockingIOError:
                pass
            with self.lock:
                lib.shifter_sensor_frame(self.s, ts.frame(*self.pose))
                if self.ticks % 10 == 0:
                    lib.shifter_buttons(self.s, self.switch_h, self.btn_b, self.btn_a)
                r1 = ctypes.create_string_buffer(5)
                lib.shifter_report1(self.s, r1)
                r36 = ctypes.create_string_buffer(64)
                lib.shifter_report36(self.s, r36)
                self.ticks += 1
            for r in (r1.raw, r36.raw):
                try:
                    self.sock.send(r)
                except BlockingIOError:
                    pass
            time.sleep(0.0005)

    def settle(self, n=60):
        start = self.ticks
        while self.ticks < start + n:
            time.sleep(0.002)

    def set(self, pose=None, switch_h=None):
        if pose is not None:
            self.pose = pose
        if switch_h is not None:
            self.switch_h = switch_h
        self.settle()

    def power_cycle(self):
        with self.lock:
            st = (ctypes.c_uint16 * 100)(*[0xFFFF] * 100)
            ts.lib.shifter_save(self.s, st)
            ts.lib.shifter_init(self.s)
            ts.lib.shifter_load(self.s, st)
        self.settle()

    def report1(self):
        with self.lock:
            r = ctypes.create_string_buffer(5)
            ts.lib.shifter_report1(self.s, r)
            return r.raw

    def settings(self):
        with self.lock:
            st = (ctypes.c_uint16 * 100)(*[0xFFFF] * 100)
            ts.lib.shifter_save(self.s, st)
            return list(st)


class Bus:
    def __init__(self, configuration='OpenShift 0.3.0', pid=fl.PID_APP):
        self.info = fl.DeviceInfo('/dev/hidraw7', pid, configuration=configuration)

    def devices(self):
        return [self.info]


class Steps(io.StringIO):
    """Answers prompts: each readline() runs the next action, then 'presses Enter'."""

    def __init__(self, actions):
        super().__init__()
        self.actions = list(actions)

    def readline(self):
        if not self.actions:
            return ''
        a = self.actions.pop(0)
        if callable(a):
            a()
            return '\n'
        return a + '\n'


class CommandLine(unittest.TestCase):
    def setUp(self):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.dev = Device(b)
        self.dev.start()
        self.dev.settle()
        self.addCleanup(a.close)
        self.addCleanup(self.stop, b)
        self.host = a

    def stop(self, b):
        self.dev.running = False
        self.dev.join()
        b.close()

    def run_ctl(self, *argv, bus=None, steps=()):
        out, err = io.StringIO(), io.StringIO()
        old, sys.stderr = sys.stderr, err
        try:
            rc = ctl.main(list(argv), bus=bus or Bus(), inp=Steps(steps), out=out,
                          opener=lambda path: os.dup(self.host.fileno()))
        finally:
            sys.stderr = old
        return rc, out.getvalue(), err.getvalue()

    def test_status(self):
        rc, out, err = self.run_ctl('status')
        self.assertEqual(rc, 0, err)
        self.assertIn('OpenShift 0.3.0', out)
        self.assertIn('balance    R 255 G 162 B 96', out)
        self.assertIn('gamma      off', out)
        self.assertIn('colour     255 182 78 (#ffb64e)', out)
        self.assertIn('logo LED   off', out)
        self.assertIn('high/low   button', out)
        self.assertIn('mode       H-pattern', out)

    def test_settings(self):
        self.assertEqual(self.run_ctl('logo', 'on')[0], 0)
        self.assertEqual(self.run_ctl('color', '#102030')[0], 0)
        self.assertEqual(self.run_ctl('highlow', 'toggle')[0], 0)
        st = self.dev.settings()
        self.assertEqual(st[0x50:0x54], [0xA503, 0x10, 0x20, 0x30])
        self.assertEqual(self.run_ctl('color', '1', '2', '3')[0], 0)
        self.assertEqual(self.run_ctl('color', 'default')[0], 0)
        self.assertEqual(self.run_ctl('logo', 'off')[0], 0)
        self.assertEqual(self.run_ctl('highlow', 'button')[0], 0)
        self.assertEqual(self.dev.settings()[0x50:0x54], [0xA500, 255, 182, 78])
        for bad in (['color', '256', '0', '0'], ['color', 'red'], ['color', '1', '2']):
            rc, _, err = self.run_ctl(*bad)
            self.assertEqual(rc, 2, bad)
            self.assertIn('error:', err)
        # Every command is a full 64-byte report.
        self.assertTrue(all(len(d) == 64 and d[0] == 0x24 for d in self.dev.received))

    def test_balance_and_gamma(self):
        rc, out, err = self.run_ctl('balance', '200', '150', '100')
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.dev.settings()[0x54:0x57], [200, 150, 100])
        self.assertEqual(self.run_ctl('balance', 'default')[0], 0)
        self.assertEqual(self.dev.settings()[0x54:0x57], [255, 162, 96])
        self.assertEqual(self.run_ctl('balance', '1', '2')[0], 2)
        rc, _, err = self.run_ctl('balance', 'b', '-10')
        self.assertEqual(rc, 2)
        self.assertIn('run "balance" alone', err)
        self.assertEqual(self.run_ctl('gamma', 'on')[0], 0)
        self.assertEqual(self.dev.settings()[0x50] & 0x08, 0x08)
        self.assertEqual(self.run_ctl('gamma', 'off')[0], 0)
        self.assertEqual(self.dev.settings()[0x50] & 0x08, 0)
        rc, _, err = self.run_ctl('balance', 'default', bus=Bus('OpenShift 0.2.1'))
        self.assertEqual(rc, 2)
        self.assertIn('0.3.0 or newer', err)

    def test_guided_balance(self):
        rc, out, err = self.run_ctl('balance', steps=['b -40', 'g+4', 'r 300', 'x', ''])
        self.assertEqual(rc, 0, err)
        self.assertIn('R 255 G 166 B 56 (saved)', out)
        self.assertIn('for example', out)
        self.dev.settle()
        self.assertEqual(self.dev.settings()[0x54:0x57], [255, 166, 56])
        cmds = [d[1:9] for d in self.dev.received]
        self.assertEqual(cmds[0], bytes([0x20, 0, 0, 0, 0, 255, 255, 255]), 'shows white')
        self.assertEqual(cmds[-1], bytes([0x20, 0, 0, 0, 0, 255, 182, 78]), 'colour put back')
        # Cancelling (or end of input) puts the old gains back.
        rc, out, _ = self.run_ctl('balance', steps=['b 10', 'q'])
        self.dev.settle()
        self.assertEqual(self.dev.settings()[0x54:0x57], [255, 166, 56])
        self.assertEqual(self.run_ctl('balance', steps=['b 10'])[0], 2)
        self.dev.settle()
        self.assertEqual(self.dev.settings()[0x54:0x57], [255, 166, 56])

    def test_needs_openshift_0_2(self):
        for conf in ('', 'OpenShift 0.1.0'):
            rc, _, err = self.run_ctl('logo', 'on', bus=Bus(conf))
            self.assertEqual(rc, 2)
            self.assertIn('0.2.0 or newer', err)
        self.assertEqual(self.dev.received, [])
        # Status still works, without the OpenShift lines.
        rc, out, _ = self.run_ctl('status', bus=Bus(''))
        self.assertEqual(rc, 0)
        self.assertIn('calibration', out)

    def test_refuses_update_mode(self):
        rc, _, err = self.run_ctl('status', bus=Bus(pid=fl.PID_BOOT))
        self.assertEqual(rc, 2)
        self.assertIn('update mode', err)

    def test_identify_leds(self):
        rc, out, err = self.run_ctl('identify-leds', steps=['handbrake', 'highlow', 'logo'])
        self.assertEqual(rc, 0, err)
        self.assertIn('position 2: logo', out)
        self.dev.settle()
        sent = [d[1:3] for d in self.dev.received]
        self.assertEqual(sent, [b'\x3f\x00', b'\x3f\x01', b'\x3f\x02', b'\x3f\xff'])

    def test_guided_calibration_then_gears(self):
        d = self.dev
        d.set((0, 0, 0x80), switch_h=1)
        steps = [lambda: None,                                   # neutral
                 lambda: d.set((-300, -1500, 0x80)),             # handbrake
                 lambda: d.set((0, 0, 0x80), switch_h=0),        # SEQ
                 lambda: d.set((0, 1400, 0x80)),                 # seq up
                 lambda: d.set((0, -1400, 0x80)),                # seq down
                 lambda: d.set((0, 0, 0x80), switch_h=1)]        # H
        steps += [lambda p=p: d.set(p) for p in ts.GEARS]
        rc, out, err = self.run_ctl('calibrate', '--sequential', steps=steps)
        self.assertEqual(rc, 0, err)
        self.assertIn('Put the switch in SEQ', out)
        self.assertIn('Put the H/SEQ switch in H', out)
        self.assertIn('gear8: ', out)
        self.assertIn('seq-down: threshold', out)
        self.assertIn('Calibration saved', out)
        self.assertEqual(self.dev.settings()[0x63], 0x1254)

        # As with Tuner, the calibration takes over from gear learning at the
        # next power-up; the tool says so.
        self.assertIn('plug it back in', out)
        d.set((0, 0, 0x80), switch_h=1)
        d.power_cycle()
        for k, (x, y, z) in enumerate(ts.GEARS, 1):
            d.set((x + 120, y - 90, z))
            self.assertEqual(d.report1()[1], 1 << (k - 1), f'gear {k}')
            d.set((0, 0, 0x80))
            self.assertEqual(d.report1()[1], 0)

    def test_single_target_checks_the_switch(self):
        self.dev.set(switch_h=0)
        rc, _, err = self.run_ctl('calibrate', 'gear3')
        self.assertEqual(rc, 2)
        self.assertIn('switch in H', err)
        rc, out, err = self.run_ctl('calibrate', 'seq-up')
        self.assertEqual(rc, 0, err)
        self.dev.set(switch_h=1)
        rc, _, err = self.run_ctl('calibrate', 'seq-up')
        self.assertEqual(rc, 2)
        self.assertIn('SEQ', err)
        self.assertEqual(self.run_ctl('calibrate', 'neutral')[0], 0)


if __name__ == '__main__':
    unittest.main()
