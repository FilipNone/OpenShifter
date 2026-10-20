"""Whole-firmware tests on an emulated shifter (tests/emu, needs unicorn).

The emulator runs the real firmware image on a Cortex-M3 core with models of
the AIR32F103 peripherals the shifter uses, the MLX90393 magnetic sensor, the
buttons, the LEDs and a USB host that enumerates the device and polls it.

OpenShift tests run on firmware/build/openshift.bin (`make -C firmware`).
Differential tests run the same scenario on the stock image and compare
everything the host can see; they are skipped unless the vendor image is at
firmware/stock/VelocityOneTuner_Shift_Firmware.bin (or $OPENSHIFT_STOCK_FW)
with the expected SHA-256.
"""
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'emu'))
sys.path.insert(0, str(HERE.parent / 'tools'))

try:
    import unicorn  # noqa: F401
except ImportError:
    raise unittest.SkipTest('unicorn is not installed (pip install -r requirements-dev.txt)')

import openshift_flash as flasher  # noqa: E402
from harness import OPENSHIFT_BIN, Rig, openshift_image, stock_image  # noqa: E402
from test_flasher import STOCK_REPORT_DESCRIPTOR  # noqa: E402
from test_shifter import led  # noqa: E402  (LED colour reference)

if not OPENSHIFT_BIN.exists():
    raise unittest.SkipTest('build the firmware first: make -C firmware')

VERSION = flasher.Image(openshift_image()).openshift_version

GEARS = [(1986, 2400, 0x80), (2400, -1886, 0x80), (40, 3175, 0x80), (70, -2536, 0x80),
         (-1956, 1720, 0x80), (-2576, -1856, 0x80), (-866, 170, 0x70), (-1116, -236, 0x70)]


def calibrate(r, hb_first=True):
    """Tuner's calibration: hold each target and send 24 21 <target>."""
    def hb():
        r.move(0, 0)
        r.command(0x24, 0x21, 9)
        r.move(-300, -1500)
        r.command(0x24, 0x21, 10)
    if hb_first:
        hb()
    for k, (x, y, z) in enumerate(GEARS, 1):
        r.move(x, y, z)
        r.command(0x24, 0x21, k)
    if not hb_first:
        hb()
    r.move(0, 0)


class EmulatedCase(unittest.TestCase):
    def assertFlashClean(self, rig):
        self.assertEqual(rig.m.flash_violations, [], 'flash writes outside the rules')

    def assertEnteredBootloader(self, rig):
        self.assertTrue(rig.reset_events(), 'no system reset requested')
        self.assertEqual(rig.m.settings(1)[0], 1, 'bootloader flag (settings halfword 0) not set')
        self.assertFlashClean(rig)


class OpenShift(EmulatedCase):
    def test_enumerates_as_the_stock_device(self):
        r = Rig(openshift_image()).boot()
        d = r.m.host.descriptors
        vid, pid, bcd = (int.from_bytes(d['device'][i:i + 2], 'little') for i in (8, 10, 12))
        self.assertEqual((vid, pid, bcd), (0x10F5, 0x7096, 0x0111))
        self.assertEqual(d['strings'][2], 'VelocityOne Multi-Shift\0\0')
        self.assertEqual(d['strings'][4], f'OpenShift {VERSION}')
        self.assertEqual(r.last(0x01), bytes.fromhex('0100000000'))
        self.assertEqual(r.last(0x24)[:2], bytes([0x24, 0x20]))
        self.assertEqual(len(r.last(0x24)), 64)

    def test_report_descriptor(self):
        """One joystick collection, as the OS class drivers need it: Linux
        maps buttons by their application collection (BTN_JOYSTICK only under
        Joystick, which udev needs for ID_INPUT_JOYSTICK) and ignores vendor
        page 0xFF00. The reports keep stock's layout."""
        r = Rig(openshift_image()).boot()
        d = r.m.host.descriptors
        self.assertEqual(d['report'], OPENSHIFT_REPORT_DESCRIPTOR)
        self.assertEqual(int.from_bytes(d['config'][25:27], 'little'), len(d['report']))
        collections, fields = parse_report_descriptor(d['report'])
        self.assertEqual(collections, [{'type': 1, 'usage': 0x00010004, 'depth': 0}])

        def bits(kind, report):
            return sum(f['size'] * f['count'] for f in fields if (f['kind'], f['report']) == (kind, report))
        self.assertEqual(bits('input', 1), 8 * (len(r.last(0x01)) - 1))
        self.assertEqual(bits('input', 0x24), 8 * 63)
        self.assertEqual(bits('output', 0x24), 8 * 63)
        self.assertEqual(bits('feature', 0x25), 8 * 15)
        self.assertEqual({(f['kind'], f['report']) for f in fields},
                         {('input', 1), ('input', 0x24), ('output', 0x24), ('feature', 0x25)})

        data = [f for f in fields if not f['const']]
        buttons = [u for f in data for u in f['usages'] if u >> 16 == 0x09]
        # Report bits 0 to 11 are buttons 2 to 13; button 1 is reserved.
        self.assertEqual(buttons, [0x00090000 | n for n in [*range(2, 14), 1]], 'one usage per button bit')
        for f in data:
            self.assertEqual(f['application'], 0x00010004)
            if f['report'] in (0x24, 0x25):
                self.assertEqual({u >> 16 for u in f['usages']}, {0xFF00})
            else:
                self.assertEqual(len(set(f['usages'])), f['count'], 'repeated usage')
        self.assertIn(0x00010032, [u for f in data for u in f['usages']], 'handbrake axis Z')

    def test_version_feature_report(self):
        """GET_REPORT(feature 0x25) gives the version, for WebHID, which
        cannot read iConfiguration. Input GET_REPORT is unchanged."""
        r = Rig(openshift_image()).boot(50)
        v = VERSION.encode()
        self.assertEqual(r.control(0xA1, 0x01, 0x0325, 0, 16), b'\x25' + v + bytes(15 - len(v)))
        self.assertEqual(r.control(0xA1, 0x01, 0x0325, 0, 4), (b'\x25' + v)[:4])
        self.assertEqual(r.control(0xA1, 0x01, 0x0101, 0, 5)[0], 0x01)
        self.assertEqual(r.control(0xA1, 0x01, 0x0125, 0, 16), 'STALL')

    def test_update_command_hands_over_to_bootloader(self):
        r = Rig(openshift_image()).boot()
        r.command(*flasher.command(flasher.CMD_BOOT), ms=0)
        r.m.run(ms=1000)
        self.assertEqual(r.m.stopped_reason, 'reset')
        self.assertEnteredBootloader(r)
        self.assertEqual(r.m.led_frames[-1][1], led(255, 255, 0) * 3, 'LEDs not yellow')

    def test_both_buttons_at_power_up_enter_bootloader(self):
        r = Rig(openshift_image())
        r.m.buttons['PA4'] = r.m.buttons['PA5'] = True
        r.run(1000)
        self.assertEnteredBootloader(r)
        self.assertEqual(r.m.host.state, 'detached', 'USB attached before recovery')

    def test_one_button_at_power_up_boots_normally(self):
        r = Rig(openshift_image())
        r.m.buttons['PA4'] = True
        r.boot()
        self.assertEqual(r.reset_events(), [])

    def test_fault_enters_bootloader(self):
        r = Rig(openshift_image()).boot()
        r.fault()
        r.m.run(ms=500)
        self.assertEnteredBootloader(r)

    def test_host_that_never_configures_gets_the_bootloader(self):
        r = Rig(openshift_image(), enumerate=False)
        r.run(400)
        r.bus_resets(8)
        r.m.run(ms=7000)
        self.assertEnteredBootloader(r)

    def test_no_host_keeps_running(self):
        r = Rig(openshift_image(), enumerate=False)
        r.run(8000)
        self.assertEqual(r.reset_events(), [])
        self.assertGreater(len(r.m.uart_packets), 1000)

    def test_calibration_persists_across_power_cycle(self):
        r = Rig(openshift_image(), h_pattern=True).boot()
        calibrate(r)
        page = r.settings_page()
        self.assertEqual(int.from_bytes(page[0xc6:0xc8], 'little'), 0x1254, 'settings magic')
        self.assertFlashClean(r)
        r = r.reboot(h_pattern=True).boot()
        self.assertEqual(r.settings_page(), page)
        for k, (x, y, z) in enumerate(GEARS, 1):
            r.move(x + 120, y - 90, z)
            self.assertEqual(r.last(0x01)[1], 1 << (k - 1), f'gear {k}')
            r.move(0, 0)
            self.assertEqual(r.last(0x01)[1], 0, f'neutral after gear {k}')

    def test_sensor_is_power_cycled_when_it_stops_answering(self):
        r = Rig(openshift_image()).boot()
        inits = sum(c == b'\x80' for c in r.m.sensor.log)
        r.m.set_raw(0, 0, 0, 0)
        r.run(400)
        self.assertFalse(r.m.sensor.powered)
        r.m.set_position(0, 0)
        r.run(2500)
        self.assertTrue(r.m.sensor.powered)
        self.assertEqual(sum(c == b'\x80' for c in r.m.sensor.log), inits + 1)

    def test_get_report_returns_live_reports(self):
        # Stock answers GET_REPORT with a fixed scratch buffer.
        r = Rig(openshift_image(), h_pattern=True).boot()
        self.assertEqual(r.control(0xA1, 0x01, 0x0101, 0, 5), bytes.fromhex('0100000000'))
        rep = r.control(0xA1, 0x01, 0x0124, 0, 64)
        self.assertEqual(rep, r.last(0x24))

    def test_set_report_runs_commands(self):
        # Stock ignores short SET_REPORTs and crashes on 64-byte ones.
        r = Rig(openshift_image(), h_pattern=True).boot()
        cmd = bytes([0x24, 0x20, 0, 0, 0, 0, 0x10, 0x20, 0x30]) + bytes(55)
        self.assertEqual(r.control(0x21, 0x09, 0x0224, 0, 64, cmd), b'')
        r.run(200)
        # Chain order handbrake, logo, high/low; logo off by default.
        c = led(0x10, 0x20, 0x30)
        self.assertEqual(r.m.led_frames[-1][1], c + bytes(3) + c)

    def test_both_reports_flow_when_the_host_polls_slowly(self):
        # Both reports are due every 2 ms and EP1 sends one per poll. 0.3.0
        # queued them, and a host polling every 2 ms got only report 1.
        for miss, least in ((lambda n: n % 7 == 0, 350), (lambda n: n % 2 == 0, 200)):
            r = Rig(openshift_image()).boot()
            r.m.host.miss = miss
            n = len(r.m.host.in_reports)
            r.run(1000)
            for rid in (0x01, 0x24):
                times = [t for t, rep in r.m.host.in_reports[n:] if rep[0] == rid]
                self.assertGreater(len(times), least, hex(rid))
                gap = max(b - a for a, b in zip(times, times[1:]))
                self.assertLess(gap, 10_000, f'report {rid:#x}: gap over 10 ms')

    def test_options_persist_and_drive_leds(self):
        r = Rig(openshift_image(), h_pattern=True).boot()
        warm = led(255, 182, 78)
        self.assertEqual(r.m.led_frames[-1][1], warm + bytes(3) + warm)
        self.assertEqual(r.last(0x24)[6:9], bytes([255, 182, 78]))
        r.command(0x24, 0x30, 30, 60, 90)
        r.command(0x24, 0x31, 1)
        r.command(0x24, 0x32, 1)
        blue, red = led(30, 60, 90), led(255, 0, 0)
        self.assertEqual(r.m.led_frames[-1][1], blue + blue + red)
        self.assertEqual(r.last(0x24)[23], 0x83)
        self.assertFlashClean(r)

        r = r.reboot(h_pattern=True).boot()
        self.assertEqual(r.m.led_frames[-1][1], blue + blue + red)
        self.assertEqual(r.last(0x24)[6:9], bytes([30, 60, 90]))
        # High/low latches; the handbrake button is button 13 in H.
        r.press('PA4', ms=100)
        r.press('PA4', hold=False, ms=100)
        self.assertEqual(r.last(0x01)[2], 0x04)
        self.assertEqual(r.m.led_frames[-1][1][6:9], led(0, 255, 0))
        r.press('PA5', ms=100)
        self.assertEqual(r.last(0x01)[2], 0x0C)
        r.press('PA5', hold=False, ms=100)
        self.assertEqual(r.last(0x01)[2], 0x04)
        self.assertEqual(r.last(0x24)[63], 0, 'still H-pattern')
        # In SEQ the handbrake LED is off, and green in handbrake mode.
        r.press('PB10', hold=False, ms=200)
        self.assertEqual(r.m.led_frames[-1][1][:3], bytes(3))
        r.press('PA5', ms=100)
        r.press('PA5', hold=False, ms=100)
        self.assertEqual(r.last(0x24)[63], 2)
        self.assertEqual(r.m.led_frames[-1][1][:3], led(0, 255, 0))
        self.assertFlashClean(r)


def as_stock_page():
    """An erased settings page with OpenShift's own settings (halfwords
    0x50..0x56) set to logo on, stock's yellow and full gains.
    Stock never touches those halfwords, so both images then drive the LEDs
    the same way."""
    page = bytearray(b'\xff' * 2048)
    for i, v in enumerate((0xA501, 255, 255, 0, 255, 255, 255)):
        page[2 * (0x50 + i):2 * (0x50 + i) + 2] = v.to_bytes(2, 'little')
    return bytes(page)


OPENSHIFT_REPORT_DESCRIPTOR = bytes.fromhex(
    '05010904a1018501 0509 1902290d 15002501 7501950c 8102 0901 9501 8102 9503 8103'
    '0501 0932 150027ffff0000 350047ffff0000 75109501 8102'
    '8524 0600ff 0901 150026ff00 7508953f 8102 0901 9102'
    '8525 0902 950f b102 c0')


def as_stock_descriptor(d):
    """OpenShift rewrites the report descriptor (checked by
    OpenShift.test_report_descriptor); everything else must match stock.
    `d` may be a read cut short by wLength."""
    if d == OPENSHIFT_REPORT_DESCRIPTOR:
        return STOCK_REPORT_DESCRIPTOR
    assert OPENSHIFT_REPORT_DESCRIPTOR.startswith(d), d.hex()
    return STOCK_REPORT_DESCRIPTOR[:len(d)]


def as_stock_hid_descriptor(d):
    """`d` with the HID descriptor in it carrying stock's report descriptor length."""
    offset = d.index(b'\x09\x21')
    n = int.from_bytes(d[offset + 7:offset + 9], 'little')
    assert n == len(OPENSHIFT_REPORT_DESCRIPTOR), n
    return d[:offset + 7] + len(STOCK_REPORT_DESCRIPTOR).to_bytes(2, 'little') + d[offset + 9:]


def parse_report_descriptor(d):
    """(collections, fields) of a HID report descriptor, with the usages and
    enclosing application collection the Linux parser (hid-core.c) assigns:
    a field with more values than usages repeats the last usage."""
    page, rid, size, count = 0, 0, 0, 0
    usages, umin = [], None
    stack, collections, fields = [], [], []
    i = 0
    while i < len(d):
        b = d[i]
        if b == 0xFE:   # long item
            i += 3 + d[i + 1]
            continue
        n = (0, 1, 2, 4)[b & 3]
        v = int.from_bytes(d[i + 1:i + 1 + n], 'little')
        tag, i = b & 0xFC, i + 1 + n
        full = (lambda u, n=n: u if n == 4 else page << 16 | u)
        if tag == 0x04:
            page = v
        elif tag == 0x84:
            rid = v
        elif tag == 0x74:
            size = v
        elif tag == 0x94:
            count = v
        elif tag == 0x08:
            usages.append(full(v))
        elif tag == 0x18:
            umin = full(v)
        elif tag == 0x28:
            usages.extend(range(umin, full(v) + 1))
        elif tag == 0xA0:
            c = {'type': v, 'usage': usages[0] if usages else 0, 'depth': len(stack)}
            collections.append(c)
            stack.append(c)
        elif tag == 0xC0:
            stack.pop()
        elif tag in (0x80, 0x90, 0xB0):
            apps = [c['usage'] for c in stack if c['type'] == 1]
            fields.append({'kind': {0x80: 'input', 0x90: 'output', 0xB0: 'feature'}[tag],
                           'report': rid, 'size': size, 'count': count, 'const': bool(v & 1),
                           'usages': [usages[min(k, len(usages) - 1)] for k in range(count)] if usages else [],
                           'application': apps[-1] if apps else 0})
        if (tag & 0x0C) == 0:   # main item: local items reset
            usages, umin = [], None
    assert not stack, 'unbalanced collections'
    return collections, fields


def seq_leds(frames):
    """Stock frames as OpenShift shows them in sequential mode: handbrake LED off."""
    return [bytes(3) + f[3:] if any(f) else f for f in frames]


class StockView(Rig):
    """OpenShift with the report 0x24 bytes stock leaves at 0 (23: options;
    24, 53, 54: white balance) checked and then cleared."""

    def last(self, report_id):
        r = super().last(report_id)
        if report_id == 0x24 and r is not None:
            assert r[23] & 0x80 and (r[24], r[53], r[54]) == (255, 255, 255), r.hex()
            r = bytearray(r)
            r[23] = r[24] = r[53] = r[54] = 0
            r = bytes(r)
        return r


@unittest.skipIf(stock_image() is None, 'stock firmware image not available')
class MatchesStock(EmulatedCase):
    """Same inputs to both images; everything the host sees must be equal,
    except for the intentional differences listed in docs/testing.md."""

    def both(self, scenario):
        def rig(img):
            cls = Rig if img is stock else StockView

            def make(image, settings=None, **kw):
                return cls(image, as_stock_page() if settings is None else settings, **kw)
            return make
        stock, ours = stock_image(), openshift_image()
        return scenario(stock, rig(stock)), scenario(ours, rig(ours))

    def test_descriptors(self):
        def sc(img, Rig):
            r = Rig(img).boot()
            d = dict(r.m.host.descriptors)
            d['strings'] = {k: v for k, v in d['strings'].items() if k != 4}
            d['config'] = d['config'][:6] + b'\0' + d['config'][7:]   # iConfiguration
            if img != stock_image():
                d['report'] = as_stock_descriptor(d['report'])
                d['config'] = as_stock_hid_descriptor(d['config'])
            return d, r.m.sensor.log[:8]
        a, b = self.both(sc)
        self.assertEqual(a, b)

    def test_standard_and_class_requests(self):
        reqs = [(0x80, 0, 0, 0, 2), (0x81, 0, 0, 0, 2), (0x82, 0, 0, 0x81, 2), (0x80, 8, 0, 0, 1),
                (0x81, 0x0A, 0, 0, 1), (0x01, 0x0B, 0, 0, 0), (0xA1, 0x02, 0, 0, 1),
                (0x21, 0x0A, 0x0100, 0, 0), (0xA1, 0x03, 0, 0, 1), (0x21, 0x0B, 1, 0, 0),
                (0x80, 6, 0x0305, 0x0409, 255), (0x80, 6, 0x03EE, 0, 255), (0x80, 6, 0x0600, 0, 10),
                (0x81, 6, 0x2100, 0, 9), (0x81, 6, 0x2200, 0, 64), (0xC0, 0x01, 0, 0, 4),
                (0x00, 3, 1, 0, 0), (0x02, 1, 0, 0x81, 0), (0x00, 9, 0, 0, 0), (0x80, 8, 0, 0, 1),
                (0x00, 9, 1, 0, 0), (0x00, 9, 2, 0, 0)]

        def sc(img, Rig):
            r = Rig(img).boot(50)
            out = [r.control(*q) for q in reqs]
            if img != stock_image():
                k = reqs.index((0x81, 6, 0x2200, 0, 64))
                out[k] = as_stock_descriptor(out[k])
                k = reqs.index((0x81, 6, 0x2100, 0, 9))
                out[k] = as_stock_hid_descriptor(out[k])
            # Accepted by both; only OpenShift acts on it (test_set_report_runs_commands).
            out.append(r.control(0x21, 0x09, 0x0224, 0, 9, bytes([0x24, 0x20, 0, 0, 0, 0, 0x44, 0x55, 0x66])))
            return out
        a, b = self.both(sc)
        for q, x, y in zip(reqs + ['SET_REPORT'], a, b):
            self.assertEqual(x, y, q)

    def test_learning_on_an_uncalibrated_unit(self):
        steps = [(0, 0, 210), (1500, 1700, 160), (0, 0, 210), (1600, -1700, 158), (100, 2000, 150),
                 (-100, -2100, 150), (-1600, 1800, 160), (-1700, -1600, 160), (-900, 400, 130),
                 (-1000, -400, 130), (0, 0, 212), (1400, 1500, 170), (1500, 1700, 160), (30, -40, 209)]

        def sc(img, Rig):
            r = Rig(img, pos=(0, 0, 210)).boot(200)
            out = []
            for x, y, z in steps:
                r.move(x, y, z)
                out.append(r.state())
            return out, r.settings_page()
        (a, pa), (b, pb) = self.both(sc)
        for i, (x, y) in enumerate(zip(a, b)):
            self.assertEqual(x, y, f'step {i} {steps[i]}')
        self.assertEqual(pa, pb)

    def test_calibration_gears_modes_and_controls(self):
        def sc(img, Rig):
            log = []
            r = Rig(img, h_pattern=True).boot()
            snap = lambda tag: log.append((tag,) + r.state())  # noqa: E731
            for k, (x, y, z) in enumerate(GEARS, 1):
                r.move(x, y, z)
                snap(f'at g{k}')
                r.command(0x24, 0x21, k)
                snap(f'cal g{k}')
            r.move(0, 0)
            r.command(0x24, 0x21, 9)
            snap('cal 9')
            r.move(-300, -1500)
            r.command(0x24, 0x21, 10)
            snap('cal 10')
            page1 = r.settings_page()
            r = r.reboot(h_pattern=True).boot()
            snap('reboot')
            for k, (x, y, z) in enumerate(GEARS, 1):
                r.move(x + 120, y - 90, z)
                snap(f'shift g{k}')
                r.move(0, 0)
                snap('n')
            r.command(0x24, 0x20, 0, 0, 0, 0, 0x10, 0x20, 0x30)
            snap('led')
            r.command(0x24, 0x23, *range(1, 14))
            snap('aux13')
            r.command(0x24, 0x24, 3, 99)
            snap('cfg3')
            r.press('PB10', hold=False, ms=200)
            snap('switch to sequential')
            for tag, y in (('seq up', 2600), ('n', 0), ('seq down', -2600), ('n', 0)):
                r.move(0, y)
                snap(tag)
            r.press('PA5', hold=True, ms=100)
            r.press('PA5', hold=False, ms=100)
            snap('B: mode')
            r.command(0x24, 0x22)
            snap('mode cycle')
            for y in (0, -400, -900, -1300, -1600, -400, 0):
                r.move(0, y, ms=120)
                snap(f'handbrake y={y}')
            r.press('PA4', hold=True, ms=100)
            r.press('PA4', hold=False, ms=100)
            snap('A toggle')
            r.run(300)
            return log, page1, r.settings_page(), [f for _, f in r.m.led_frames[-3:]]
        a, b = self.both(sc)
        for x, y in zip(a[0], b[0]):
            self.assertEqual(x, y, x[0])
        self.assertEqual(a[1], b[1], 'settings after calibration')
        self.assertEqual(a[2], b[2], 'settings at the end')
        # Ends in sequential mode; LED frame timing differs, so only the
        # final colours are compared.
        self.assertEqual(seq_leds(a[3][-1:]), b[3][-1:], 'LED frames')

    def test_power_up_in_gear(self):
        def sc(img, Rig):
            r = Rig(img, h_pattern=True).boot()
            calibrate(r, hb_first=False)
            r = Rig(img, r.settings_page(), h_pattern=True, pos=(2106, 2310, 0x80)).boot(400)
            out = [r.state()]
            r.move(0, 0, ms=300)
            out.append(r.state())
            r.move(2106, 2310, ms=300)
            out.append(r.state())
            return out
        a, b = self.both(sc)
        self.assertEqual(a, b)
        self.assertEqual([s[0][1] for s in a], [0, 0, 1])

    def test_uart_stream_without_usb_host(self):
        def sc(img, Rig):
            r = Rig(img, enumerate=False)
            r.run(1500)
            return [p for _, p in r.m.uart_packets[:200]]
        a, b = self.both(sc)
        self.assertGreater(len(a), 150)
        self.assertEqual(a, b)

    def test_suspend_and_resume(self):
        def sc(img, Rig):
            r = Rig(img).boot(300)
            n = len(r.m.led_frames)
            r.suspend(1500)
            off = [f for _, f in r.m.led_frames[n:]]
            asleep = r.m.sensor.powered
            n = len(r.m.led_frames)
            r.resume(3000)
            return off, asleep, [f for _, f in r.m.led_frames[n:]], r.m.sensor.powered, r.state()
        a, b = self.both(sc)
        self.assertEqual(a[:2] + (seq_leds(a[2]),) + a[3:], b)
        self.assertEqual(a[0], [bytes(9)], 'LEDs off while suspended')


if __name__ == '__main__':
    unittest.main()
