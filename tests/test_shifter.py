"""Host tests of the portable shifter core (firmware/src/shifter.c).

The core is compiled for the host with trapping UBSan and called through
ctypes. Its integer re-implementations of the stock float arithmetic are
checked against Python: float64 arithmetic rounded to float32 with struct is
exact for *, / and sqrt of float32 inputs (53 >= 2*24 + 2, so double rounding
is innocuous). Needs clang or gcc; no third-party Python packages.
"""
import ctypes
import math
import os
import pathlib
import random
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / 'firmware' / 'src'
SHIM = ROOT / 'tests' / 'native' / 'shim.c'

SH_FRAME_OK, SH_FRAME_EMPTY, SH_FRAME_POWER_OFF = 0, 1, 2
SH_ACT_SAVE, SH_ACT_LED, SH_ACT_BOOT = 1, 2, 4


def _define(name):
    m = re.search(rf'^#define {name}\s+(\d+)u?\b', (SRC / 'shifter.h').read_text(), re.M)
    return int(m.group(1))


# Settings write timing, from shifter.h (also used by test_emulated.py).
SAVE_QUIET_MS = _define('SH_SAVE_QUIET_MS')
SAVE_BURST = _define('SH_SAVE_BURST')
SAVE_REFILL_MS = _define('SH_SAVE_REFILL_MS')

lib = None
_tmp = None


def setUpModule():
    global lib, _tmp
    cc = os.environ.get('CC') or shutil.which('clang') or shutil.which('cc') or shutil.which('gcc')
    if not cc:
        raise unittest.SkipTest('no C compiler')
    _tmp = tempfile.TemporaryDirectory()
    so = pathlib.Path(_tmp.name) / 'libshifter.so'
    base = [cc, '-std=c11', '-O2', '-shared', '-fPIC', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-function', f'-I{SRC}', str(SHIM), '-o', str(so)]
    for extra in (['-fsanitize=undefined', '-fsanitize-trap=undefined'], []):
        r = subprocess.run(base[:1] + extra + base[1:], capture_output=True, text=True)
        if r.returncode == 0:
            break
    else:
        raise RuntimeError(f'host build failed:\n{r.stderr}')
    lib = ctypes.CDLL(str(so))
    u32, i32, u16, i16 = ctypes.c_uint32, ctypes.c_int, ctypes.c_uint16, ctypes.c_int16
    for name, res, args in (
            ('t_sf_mul', u32, [u32, u32]), ('t_sf_div', u32, [u32, u32]),
            ('t_sf_from_int', u32, [i32]), ('t_sf_to_int', i32, [u32]),
            ('t_sf_sqrt_int', u32, [u32]), ('t_tilt_of', i32, [u16, i16]),
            ('sh_isqrt', u32, [u32]), ('t_sizeof', ctypes.c_uint, []),
            ('sh_sincos_q30', None, [u32, ctypes.POINTER(i32), ctypes.POINTER(i32)]),
            ('shifter_sensor_frame', i32, [ctypes.c_void_p, ctypes.c_char_p]),
            ('shifter_command', ctypes.c_uint, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint]),
            ('shifter_buttons', ctypes.c_uint, [ctypes.c_void_p, i32, i32, i32]),
            ('shifter_led_grb', None, [ctypes.c_void_p, i32, ctypes.c_char_p]),
            ('shifter_report36', None, [ctypes.c_void_p, ctypes.c_char_p]),
            ('shifter_report_raw', None, [ctypes.c_void_p, ctypes.c_char_p]),
            ('shifter_raw_wanted', i32, [ctypes.c_void_p]),
            ('sh_saves_init', None, [ctypes.c_void_p]),
            ('sh_saves_changed', None, [ctypes.c_void_p, u32]),
            ('sh_saves_take', i32, [ctypes.c_void_p, u32, i32]),
            ('sh_saves_spent', None, [ctypes.c_void_p]),
            ('t_saves_sizeof', ctypes.c_uint, [])):
        f = getattr(lib, name)
        f.restype, f.argtypes = res, args


def tearDownModule():
    if _tmp:
        _tmp.cleanup()


def f32(x):
    return struct.unpack('<f', struct.pack('<f', x))[0]


def bits(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def val(b):
    return struct.unpack('<f', struct.pack('<I', b))[0]


def rand_float_bits(rng, exp_lo=100, exp_hi=154, signed=False):
    b = rng.randrange(exp_lo, exp_hi) << 23 | rng.getrandbits(23)
    if signed and rng.random() < 0.5:
        b |= 0x80000000
    return b


class SoftFloat(unittest.TestCase):
    N = 100_000

    def setUp(self):
        self.rng = random.Random(1234)

    def test_mul(self):
        for _ in range(self.N):
            a, b = rand_float_bits(self.rng, signed=True), rand_float_bits(self.rng, signed=True)
            self.assertEqual(lib.t_sf_mul(a, b), bits(val(a) * val(b)), (hex(a), hex(b)))
        self.assertEqual(lib.t_sf_mul(0, bits(3.0)), 0)

    def test_div(self):
        for _ in range(self.N):
            a, b = rand_float_bits(self.rng), rand_float_bits(self.rng)
            self.assertEqual(lib.t_sf_div(a, b), bits(val(a) / val(b)), (hex(a), hex(b)))
        self.assertEqual(lib.t_sf_div(0, bits(3.0)), 0)

    def test_int_conversions(self):
        edge = [0, 1, 2, 3, (1 << 24) - 1, 1 << 24, (1 << 24) + 1, (1 << 24) + 3, (1 << 31) - 1]
        for v in edge + [self.rng.randrange(1 << 31) for _ in range(self.N)]:
            self.assertEqual(lib.t_sf_from_int(v), bits(float(v)), v)
        for _ in range(self.N):
            b = rand_float_bits(self.rng, 100, 157, signed=True)
            self.assertEqual(lib.t_sf_to_int(b), int(val(b)), hex(b))

    def test_sqrt_matches_float_of_double_sqrt(self):
        cases = list(range(1 << 16))
        cases += [k * k + d for k in range(0, 65536, 97) for d in (-1, 0, 1) if k * k + d >= 0]
        cases += [self.rng.randrange(1 << 32) for _ in range(self.N)]
        cases += [(1 << 32) - 1]
        for r2 in cases:
            self.assertEqual(lib.t_sf_sqrt_int(r2), bits(f32(math.sqrt(r2))), r2)

    def test_tilt_matches_stock_float_path(self):
        cases = [(0, 0), (0, 5), (0, -5), (1, 0), (65535, 32767), (65535, -32768), (1, 32767), (1, -32768)]
        cases += [(self.rng.randrange(65536), self.rng.randrange(-32768, 32768)) for _ in range(self.N)]
        cases += [(self.rng.randrange(4000), self.rng.randrange(-2600, 2600)) for _ in range(self.N)]
        for s12, s14 in cases:
            want = int(f32(f32(math.atan2(s14, s12)) * f32(1000.0)))
            self.assertEqual(lib.t_tilt_of(s12, s14), want, (s12, s14))


class IntegerMath(unittest.TestCase):
    def test_isqrt(self):
        rng = random.Random(7)
        cases = list(range(1 << 16)) + [(1 << 32) - 1] + [rng.randrange(1 << 32) for _ in range(100_000)]
        cases += [k * k - 1 for k in range(1, 65536, 13)]
        for v in cases:
            self.assertEqual(lib.sh_isqrt(v), math.isqrt(v), v)

    def test_sincos_q30(self):
        rng = random.Random(9)
        s, c = ctypes.c_int(), ctypes.c_int()
        angles = [0, 1 << 30, 1 << 31, 3 << 30, 0x40000001, 0xBFFFFFFF, 0xFFFFFFFF]
        angles += [rng.getrandbits(32) for _ in range(50_000)]
        worst = 0
        for a in angles:
            lib.sh_sincos_q30(a, ctypes.byref(s), ctypes.byref(c))
            t = 2 * math.pi * a / 2 ** 32
            worst = max(worst, abs(s.value - math.sin(t) * 2 ** 30), abs(c.value - math.cos(t) * 2 ** 30))
        self.assertLess(worst, 64, 'sin/cos error in Q30 LSBs')


def frame(x, y, z_hi=0x80, t=0x8000):
    """The 8 bytes the firmware reads for a magnet at (x, y); see stm32emu.set_position."""
    mag = math.hypot(x, y)
    raw_y = min(0xFFFF, int(round(mag / 0.60725)))
    raw_x = int(round((math.atan2(x, y) % (2 * math.pi)) * 182.04444444444445 * 57.32484076433121)) & 0xFFFF
    return bytes([0x00]) + struct.pack('>HHH', t, raw_x, raw_y) + bytes([z_hi])


GEARS = [(1986, 2400, 0x80), (2400, -1886, 0x80), (40, 3175, 0x80), (70, -2536, 0x80),
         (-1956, 1720, 0x80), (-2576, -1856, 0x80), (-866, 170, 0x70), (-1116, -236, 0x70)]


class CoreHelpers:
    def new(self, settings=None):
        s = ctypes.create_string_buffer(lib.t_sizeof())
        st = (ctypes.c_uint16 * 100)(*(settings or [0xFFFF] * 100))
        lib.shifter_init(s)
        lib.shifter_load(s, st)
        return s

    def feed(self, s, x, y, z=0x80, n=40):
        for _ in range(n):
            lib.shifter_sensor_frame(s, frame(x, y, z))

    def report1(self, s):
        out = ctypes.create_string_buffer(5)
        lib.shifter_report1(s, out)
        return out.raw

    def cmd(self, s, *b):
        return lib.shifter_command(s, bytes(b), len(b))


class Core(CoreHelpers, unittest.TestCase):
    def test_empty_frames_power_cycle_sensor(self):
        s = self.new()
        for i in range(1, 100):
            self.assertEqual(lib.shifter_sensor_frame(s, bytes(8)), SH_FRAME_EMPTY, i)
        self.assertEqual(lib.shifter_sensor_frame(s, bytes(8)), SH_FRAME_POWER_OFF)
        self.assertEqual(lib.shifter_sensor_frame(s, frame(0, 0)), SH_FRAME_OK)
        self.assertEqual(lib.shifter_sensor_frame(s, bytes(8)), SH_FRAME_EMPTY)

    def test_commands(self):
        s = self.new()
        self.assertEqual(self.cmd(s, 0x24, 0x00), SH_ACT_SAVE | SH_ACT_BOOT)
        self.assertEqual(self.cmd(s, 0x24, 0x20, 0, 0, 0, 0, 1, 2, 3), SH_ACT_LED)
        self.assertEqual(self.cmd(s, 0x24, 0x21, 0), 0)
        self.assertEqual(self.cmd(s, 0x24, 0x21, 11), 0)
        self.assertEqual(self.cmd(s, 0x24, 0x7f), 0)
        self.assertEqual(self.cmd(s, 0x01, 0x00), 0)
        self.assertEqual(self.cmd(s, 0x24), 0)
        self.assertEqual(lib.shifter_command(s, bytes(200), 200), 0)

    def test_calibration_round_trip_and_gears(self):
        # Handbrake targets first: until the unit is calibrated the stock
        # learner takes a steady (-300, -1500) for gear 4 and keeps its
        # smaller radius, which would then hide the real gear 4.
        s = self.new()
        self.feed(s, 0, 0)
        self.cmd(s, 0x24, 0x21, 9)
        self.feed(s, -300, -1500)
        self.cmd(s, 0x24, 0x21, 10)
        for k, (x, y, z) in enumerate(GEARS, 1):
            self.feed(s, x, y, z)
            self.assertEqual(self.cmd(s, 0x24, 0x21, k), SH_ACT_SAVE)
        st = (ctypes.c_uint16 * 100)(*[0xFFFF] * 100)
        lib.shifter_save(s, st)
        self.assertEqual(st[0x63], 0x1254, 'settings magic')

        # A power cycle: the matcher comes back from the saved gear table.
        t = self.new(list(st))
        self.assertEqual(lib.t_settings_valid(t), 1)
        m = [ctypes.c_int() for _ in range(3)]
        for i in range(8):
            lib.t_match(t, i, *map(ctypes.byref, m))
            saved = [ctypes.c_int16(st[7 + 4 * i]).value, ctypes.c_int16(st[8 + 4 * i]).value,
                     ctypes.c_int16(st[0x37 + i]).value]
            self.assertEqual([v.value for v in m], saved, f'gear {i + 1}')

        # As in stock, the first pose after power-up is taken as neutral: a
        # unit switched on while in gear reports nothing until it is moved
        # through neutral.
        self.feed(t, 2106, 2310)
        self.assertEqual(self.report1(t)[1], 0)
        self.feed(t, 0, 0)
        for k, (x, y, z) in enumerate(GEARS, 1):
            self.feed(t, x + 120, y - 90, z)
            self.assertEqual(self.report1(t)[1], 1 << (k - 1), f'gear {k}')
            self.feed(t, 0, 0)
            self.assertEqual(self.report1(t)[1], 0, f'neutral after gear {k}')

        # Through neutral faster than the debounce: gear 3 straight to gear
        # 2 reports neutral for 5 samples in between, so hosts see gear 3
        # released before gear 2 is pressed.
        (x3, y3, z3), (x2, y2, z2) = GEARS[2], GEARS[1]
        self.feed(t, x3, y3, z3)
        seen = []
        for _ in range(40):
            lib.shifter_sensor_frame(t, frame(x2, y2, z2))
            seen.append(self.report1(t)[1])
        self.assertEqual(seen, [0b100] * 5 + [0] * 5 + [0b10] * 30)
        # Through neutral at a normal pace: no extra gap.
        self.feed(t, 0, 0)
        seen = []
        for _ in range(25):
            lib.shifter_sensor_frame(t, frame(x3, y3, z3))
            seen.append(self.report1(t)[1])
        self.assertEqual(seen, [0] * 5 + [0b100] * 20)


DEFAULT_GAINS = (255, 162, 96)


def gamma22(v):
    return 0 if v == 0 else max(1, round(255 * (v / 255) ** 2.2))


def led(r, g, b, gains=DEFAULT_GAINS, gamma=False):
    """One LED as it goes on the wire: G, R, B after the optional gamma, white
    balance and stock's 2/3 brightness cap."""
    def level(c, v):
        return (gamma22(v) if gamma else v) * gains[c] // 255 * 2 // 3
    return bytes([level(1, g), level(0, r), level(2, b)])


OFF = bytes(3)
WARM = led(255, 182, 78)


class Options(CoreHelpers, unittest.TestCase):
    """OpenShifter settings: logo LED, colour, handbrake and high/low buttons."""

    def leds(self, s, asleep=0):
        out = ctypes.create_string_buffer(9)
        lib.shifter_led_grb(s, asleep, out)
        return [out.raw[i:i + 3] for i in (0, 6, 3)]   # handbrake, high/low, logo

    def report36(self, s):
        out = ctypes.create_string_buffer(64)
        lib.shifter_report36(s, out)
        return out.raw

    def scan(self, s, h, b=0, a=0, n=1):
        act = 0
        for _ in range(n):
            act |= lib.shifter_buttons(s, h, b, a)
        return act

    def click(self, s, h, b=0, a=0):
        """Press for two scans, then release."""
        return self.scan(s, h, b, a, 2) | self.scan(s, h)

    def save(self, s):
        st = (ctypes.c_uint16 * 100)(*[0xFFFF] * 100)
        lib.shifter_save(s, st)
        return list(st)

    def test_defaults(self):
        s = self.new()
        self.scan(s, 1)
        self.assertEqual(self.leds(s), [WARM, WARM, OFF])
        r = self.report36(s)
        self.assertEqual(r[6:9], bytes([255, 182, 78]))
        self.assertEqual(r[23], 0x80)
        self.assertEqual(self.leds(s, asleep=1), [OFF] * 3)

    def test_commands(self):
        s = self.new()
        self.scan(s, 1)
        self.assertEqual(self.cmd(s, 0x24, 0x31, 1), SH_ACT_LED | SH_ACT_SAVE)
        self.assertEqual(self.leds(s), [WARM, WARM, WARM])
        self.assertEqual(self.cmd(s, 0x24, 0x30, 10, 200, 30), SH_ACT_LED | SH_ACT_SAVE)
        self.assertEqual(self.leds(s), [led(10, 200, 30)] * 3)
        self.assertEqual(self.report36(s)[6:9], bytes([10, 200, 30]))
        self.assertEqual(self.cmd(s, 0x24, 0x31, 0), SH_ACT_LED | SH_ACT_SAVE)
        self.assertEqual(self.leds(s)[2], OFF)
        for bad in ((0x31, 2), (0x32, 2), (0x3f, 3)):
            self.assertEqual(self.cmd(s, 0x24, *bad), 0, bad)
        # 24 20 (Tuner) changes the live colour only.
        self.assertEqual(self.cmd(s, 0x24, 0x20, 0, 0, 0, 0, 1, 2, 3), SH_ACT_LED)
        self.assertEqual(self.report36(s)[6:9], bytes([1, 2, 3]))
        self.assertEqual(self.save(s)[0x51:0x54], [10, 200, 30])

    def test_settings_round_trip(self):
        s = self.new()
        self.cmd(s, 0x24, 0x30, 1, 2, 3)
        self.cmd(s, 0x24, 0x31, 1)
        self.cmd(s, 0x24, 0x32, 1)
        st = self.save(s)
        self.assertEqual(st[0x50:0x54], [0xA503, 1, 2, 3])
        t = self.new(st)
        self.assertEqual(self.report36(t)[6:9], bytes([1, 2, 3]))
        self.assertEqual(self.report36(t)[23], 0x83)
        # An erased or zeroed page, or one without the marker, means the
        # defaults; so does an out of range colour.
        for bad in ([0xFFFF] * 4, [0] * 4, [3, 1, 2, 3], [0xA501, 0x100, 0, 0]):
            st[0x50:0x54] = bad
            t = self.new(st)
            self.assertEqual(self.report36(t)[6:9], bytes([255, 182, 78]), bad)
            self.assertEqual(self.report36(t)[23], 0x81 if bad[0] == 0xA501 else 0x80, bad)
        t = self.new([0] * 100)
        self.assertEqual(self.report36(t)[6:9], bytes([255, 182, 78]), 'all-zero page')

    def test_handbrake_button(self):
        s = self.new()
        self.scan(s, 1)
        # H: a plain button (report 1, bit 3); the mode stays H-pattern.
        self.scan(s, 1, b=1, n=3)
        self.assertEqual(self.report1(s)[2], 0x08)
        self.scan(s, 1)
        self.assertEqual(self.report1(s)[2], 0)
        self.assertEqual(self.report36(s)[63], 0)
        self.assertEqual(self.leds(s)[0], WARM)
        # SEQ: the LED is off, and green while handbrake mode is on.
        self.scan(s, 0)
        self.assertEqual(self.report36(s)[63], 1)
        self.assertEqual(self.leds(s)[0], OFF)
        self.scan(s, 0, b=1, n=2)
        self.assertEqual(self.report1(s)[2], 0, 'no button bit in SEQ')
        self.assertTrue(self.scan(s, 0) & SH_ACT_LED)
        self.assertEqual(self.report36(s)[63], 2)
        self.assertEqual(self.leds(s)[0], led(0, 255, 0), 'green in handbrake mode')
        self.click(s, 0, b=1)
        self.assertEqual(self.report36(s)[63], 1)
        self.assertEqual(self.leds(s)[0], OFF)
        # Back to H: the colour again.
        self.scan(s, 1)
        self.assertEqual(self.report36(s)[63], 0)
        self.assertEqual(self.leds(s)[0], WARM)

    def test_neutral_button(self):
        s = self.new()
        self.scan(s, 1)
        self.feed(s, 0, 0)
        self.cmd(s, 0x24, 0x21, 9)
        self.feed(s, -300, -1500)
        self.cmd(s, 0x24, 0x21, 10)
        for k, (x, y, z) in enumerate(GEARS, 1):
            self.feed(s, x, y, z)
            self.cmd(s, 0x24, 0x21, k)
        s = self.new(self.save(s))
        self.scan(s, 1)
        self.feed(s, 0, 0)
        self.assertEqual(self.report1(s)[2], 0, 'off by default')

        self.assertEqual(self.cmd(s, 0x24, 0x35, 1), SH_ACT_SAVE)
        self.assertEqual(self.cmd(s, 0x24, 0x35, 2), 0)
        self.assertEqual(self.report36(s)[23], 0x90)
        self.assertEqual(self.save(s)[0x50], 0xA510)
        # H: report 1 bit 12 (button 1) while no gear is engaged.
        self.assertEqual(self.report1(s)[1:3], bytes([0, 0x10]))
        for k, (x, y, z) in enumerate(GEARS, 1):
            self.feed(s, x + 120, y - 90, z)
            self.assertEqual(self.report1(s)[1:3], bytes([1 << (k - 1), 0]), f'gear {k}')
            self.feed(s, 0, 0)
            self.assertEqual(self.report1(s)[1:3], bytes([0, 0x10]), f'neutral after gear {k}')
        # Saved; never set in sequential or handbrake mode.
        s = self.new(self.save(s))
        self.scan(s, 1)
        self.feed(s, 0, 0)
        self.assertEqual(self.report1(s)[2], 0x10)
        self.scan(s, 0)
        self.feed(s, 0, 0)
        self.assertEqual(self.report1(s)[2], 0, 'not in sequential mode')
        self.click(s, 0, b=1)
        self.assertEqual(self.report36(s)[63], 2)
        self.feed(s, 0, 0)
        self.assertEqual(self.report1(s)[2], 0, 'not in handbrake mode')
        self.scan(s, 1)
        self.feed(s, 0, 0)
        self.assertEqual(self.cmd(s, 0x24, 0x35, 0), SH_ACT_SAVE)
        self.assertEqual(self.report1(s)[2], 0)
        self.assertEqual(self.report36(s)[23], 0x80)

    def test_high_low_button(self):
        s = self.new()
        self.scan(s, 1)
        self.scan(s, 1, a=1, n=3)
        self.assertEqual(self.report1(s)[2], 0x04)
        self.scan(s, 1)
        self.assertEqual(self.report1(s)[2], 0)
        self.assertEqual(self.leds(s)[1], WARM)

        self.cmd(s, 0x24, 0x32, 1)
        red, green = led(255, 0, 0), led(0, 255, 0)
        self.assertEqual(self.leds(s)[1], red)
        self.assertEqual(self.report36(s)[23], 0x82)
        self.assertTrue(self.scan(s, 1, a=1) & SH_ACT_LED)
        self.scan(s, 1, a=1, n=10)
        self.scan(s, 1, n=5)
        self.assertEqual(self.report1(s)[2], 0x04, 'latched on')
        self.assertEqual(self.leds(s)[1], green)
        self.assertEqual(self.report36(s)[23], 0x86)
        self.click(s, 1, a=1)
        self.assertEqual(self.report1(s)[2], 0)
        self.assertEqual(self.leds(s)[1], red)
        # The latch also works in SEQ, and switching back to button mode clears it.
        self.click(s, 0, a=1)
        self.assertEqual(self.report1(s)[2], 0x04)
        self.cmd(s, 0x24, 0x32, 0)
        self.assertEqual(self.report1(s)[2], 0)
        self.assertEqual(self.leds(s)[1], WARM)

    def test_colour_correction(self):
        s = self.new()
        self.scan(s, 1)
        r = self.report36(s)
        self.assertEqual((r[24], r[53], r[54]), DEFAULT_GAINS)
        self.assertEqual(r[23], 0x80, 'gamma off by default')
        # Gamma, every level, with full gains.
        self.cmd(s, 0x24, 0x33, 255, 255, 255, 0)
        self.assertEqual(self.cmd(s, 0x24, 0x34, 1), SH_ACT_LED | SH_ACT_SAVE)
        self.assertEqual(self.report36(s)[23], 0x88)
        for v in range(256):
            self.cmd(s, 0x24, 0x20, 0, 0, 0, 0, v, v, v)
            self.assertEqual(self.leds(s)[0], bytes([gamma22(v) * 2 // 3]) * 3, v)
        self.assertEqual(self.cmd(s, 0x24, 0x34, 2), 0)
        # Gains, saved only when asked.
        self.assertEqual(self.cmd(s, 0x24, 0x33, 200, 100, 50, 0), SH_ACT_LED)
        self.assertEqual(self.cmd(s, 0x24, 0x33, 200, 100, 50, 1), SH_ACT_LED | SH_ACT_SAVE)
        self.cmd(s, 0x24, 0x20, 0, 0, 0, 0, 255, 128, 64)
        self.assertEqual(self.leds(s)[0], led(255, 128, 64, (200, 100, 50), gamma=True))
        r = self.report36(s)
        self.assertEqual((r[24], r[53], r[54]), (200, 100, 50))
        self.assertEqual(self.cmd(s, 0x24, 0x33, 1, 2, 3, 2), 0)
        st = self.save(s)
        self.assertEqual(st[0x50], 0xA508)
        self.assertEqual(st[0x54:0x57], [200, 100, 50])
        t = self.new(st)
        self.assertEqual(self.report36(t)[23], 0x88)
        # Gamma off and full gains give stock's output.
        self.cmd(t, 0x24, 0x34, 0)
        self.cmd(t, 0x24, 0x33, 255, 255, 255, 0)
        self.cmd(t, 0x24, 0x20, 0, 0, 0, 0, 255, 128, 64)
        self.scan(t, 1)
        self.assertEqual(self.leds(t)[0], bytes([128 * 2 // 3, 255 * 2 // 3, 64 * 2 // 3]))
        # Without the marker the gains are the defaults.
        st[0x50] = 0
        r = self.report36(self.new(st))
        self.assertEqual((r[24], r[53], r[54]), DEFAULT_GAINS)

    def test_led_identify_and_update(self):
        s = self.new()
        self.scan(s, 1)
        white = led(255, 255, 255)
        for i in range(3):
            self.assertEqual(self.cmd(s, 0x24, 0x3f, i), SH_ACT_LED)
            out = ctypes.create_string_buffer(9)
            lib.shifter_led_grb(s, 0, out)
            self.assertEqual(out.raw, bytes(3 * i) + white + bytes(6 - 3 * i))
        self.cmd(s, 0x24, 0x3f, 0xff)
        self.assertEqual(self.leds(s), [WARM, WARM, OFF])
        self.cmd(s, 0x24, 0x00)
        self.assertEqual(self.leds(s), [led(255, 255, 0)] * 3)


class AntiJitter(CoreHelpers, unittest.TestCase):
    """24 36: anti-jitter on the lever position, at each level."""

    def calibrated(self, level):
        """A calibrated unit (rest y 0, pulled y -1500) in H, at `level`."""
        s = self.new()
        lib.shifter_buttons(s, 1, 0, 0)
        self.feed(s, 0, 0)
        self.cmd(s, 0x24, 0x21, 9)
        self.feed(s, -300, -1500)
        self.cmd(s, 0x24, 0x21, 10)
        for k, (x, y, z) in enumerate(GEARS, 1):
            self.feed(s, x, y, z)
            self.cmd(s, 0x24, 0x21, k)
        st = (ctypes.c_uint16 * 100)(*[0xFFFF] * 100)
        lib.shifter_save(s, st)
        s = self.new(list(st))
        lib.shifter_buttons(s, 1, 0, 0)
        self.assertEqual(self.cmd(s, 0x24, 0x36, level), SH_ACT_SAVE)
        return s

    def to_handbrake_mode(self, s):
        for b in (0, 1, 1, 0):
            lib.shifter_buttons(s, 0, b, 0)
        self.assertEqual(self.report36(s)[63], 2)

    def report36(self, s):
        out = ctypes.create_string_buffer(64)
        lib.shifter_report36(s, out)
        return out.raw

    def axis(self, s):
        r = self.report1(s)
        return r[3] | r[4] << 8

    def run_frames(self, s, poses):
        """Feeds (x, y, z) frames; returns (gears, axis, x, y, r/8) after each."""
        out = []
        for x, y, z in poses:
            lib.shifter_sensor_frame(s, frame(x, y, z))
            r1, r36 = self.report1(s), self.report36(s)
            out.append((r1[1], r1[3] | r1[4] << 8, struct.unpack('>hh', r36[2:6]), r36[48]))
        return out

    @staticmethod
    def changes(rows, i):
        return sum(a[i] != b[i] for a, b in zip(rows, rows[1:]))

    def test_command_and_storage(self):
        s = self.new()
        for level in range(4):
            self.assertEqual(self.cmd(s, 0x24, 0x36, level), SH_ACT_SAVE)
            self.assertEqual(self.report36(s)[23], 0x80 | level << 5)
        self.assertEqual(self.cmd(s, 0x24, 0x36, 4), 0)
        self.assertEqual(self.report36(s)[23] >> 5 & 3, 3, 'a bad level changes nothing')
        st = (ctypes.c_uint16 * 100)(*[0xFFFF] * 100)
        lib.shifter_save(s, st)
        self.assertEqual(st[0x50], 0xA560)
        self.assertEqual(self.report36(self.new(list(st)))[23], 0xE0, 'kept after a restart')
        self.assertEqual(self.report36(self.new())[23], 0x80, 'none by default')

    def test_lever_position_holds_still(self):
        rng = random.Random(7)
        poses = {'neutral': (120, -280), 'gear 3': (60, 3150), 'handbrake': (-150, -850)}
        for name, (x0, y0) in poses.items():
            noise = [(x0 + rng.randint(-3, 3), y0 + rng.randint(-3, 3), 0x80 + rng.randint(0, 1))
                     for _ in range(600)]
            for level in range(4):
                s = self.calibrated(level)
                if name == 'handbrake':
                    self.to_handbrake_mode(s)
                rows = self.run_frames(s, noise)[100:]
                moved = [self.changes(rows, i) for i in (1, 2, 3)]   # axis, x/y, r
                if level == 0:
                    self.assertGreater(moved[1], 100, f'{name}: the raw x/y follow the noise')
                    if name == 'neutral':   # where z decides the field strength
                        self.assertGreater(moved[2], 50, f'{name}: the raw r follows z')
                else:
                    # Low may still step once in a while near neutral, where
                    # x and y are noisiest; never back and forth.
                    allowed = 3 if level == 1 else 0
                    self.assertLessEqual(max(moved), allowed, f'{name} at level {level}: {moved}')
                self.assertEqual(self.changes(rows, 0), 0, f'{name}: gears at level {level}')

    def test_gear_7_does_not_chatter(self):
        """Gear 7 is held while its field strength stays under a limit, and
        that strength steps with z. With the lever right at the limit and z
        flipping between the steps on either side of it, the raw reading
        keeps dropping and taking the gear. Anti-jitter settles on one side
        and stays there."""
        rng = random.Random(11)
        x, y, _ = GEARS[6]
        poses = [(x, y, rng.choice((0x6E, 0x6F))) for _ in range(1500)]
        for level in range(4):
            s = self.calibrated(level)
            self.run_frames(s, [GEARS[6]] * 40)
            self.assertEqual(self.report1(s)[1], 0x40)
            flips = self.changes(self.run_frames(s, poses), 0)
            if level == 0:
                self.assertGreater(flips, 4, 'the raw reading chatters')
            else:
                self.assertLessEqual(flips, 1, level)

    def test_quick_moves_are_not_late(self):
        """Shifts and a yanked handbrake: every level gives the same reports
        at the same samples as none."""
        def path(steps, *points):
            out = []
            for (x0, y0, z0), (x1, y1, z1) in zip(points, points[1:]):
                out += [(round(x0 + (x1 - x0) * i / steps), round(y0 + (y1 - y0) * i / steps), z1 if i else z0)
                        for i in range(steps)] + [(x1, y1, z1)] * 30
            return out
        n = (0, 0, 0x80)
        shifts = path(8, n, GEARS[0], n, GEARS[2], GEARS[3], n, GEARS[5], n)
        yank = path(10, n, (-150, -1700, 0x80), n, (-150, -1700, 0x80), n)
        ref = None
        for level in range(4):
            s = self.calibrated(level)
            got = [r[0] for r in self.run_frames(s, shifts)]
            self.to_handbrake_mode(s)
            got += [r[1] for r in self.run_frames(s, yank)]
            ref = ref or got
            self.assertEqual(got, ref, level)
        self.assertIn(65535, ref)

    def test_follows_slow_moves(self):
        span = 1100   # axis from rest - 300 (-300) to pulled + 100 (-1400)
        ramp = [round(-300 - 1100 * i / 300) for i in range(300)] + [-1400] * 100
        back = [round(-1400 + 900 * i / 200) for i in range(200)] + [-500] * 100
        poses = [(-150, y, 0x80) for y in ramp + back]
        s = self.calibrated(0)
        self.to_handbrake_mode(s)
        ref = [r[1] for r in self.run_frames(s, poses)]
        speed = 4.5   # y per sample, the faster of the two moves
        for level, band, shift in ((1, 4, 1), (2, 8, 1), (3, 16, 2)):
            s = self.calibrated(level)
            self.to_handbrake_mode(s)
            out = [r[1] for r in self.run_frames(s, poses)]
            worst = max(abs(a - b) for a, b in zip(out, ref))
            # The band's free travel when the lever turns round, plus the
            # running average's lag.
            limit = 2 * band + (2 ** shift - 1) * speed + 2
            self.assertLess(worst, limit * 65535 / span, level)
            # Where the lever stopped: within the band (frame() rounds y by 1 or 2).
            for at in (399, len(out) - 1):
                self.assertLessEqual(abs(out[at] - ref[at]), (band + 2) * 65535 / span, (level, at))

    def test_calibration_with_noise(self):
        """Calibrated at high with a noisy sensor, every gear is found."""
        rng = random.Random(5)
        def noisy(x, y, z, n=60):
            return [(x + rng.randint(-3, 3), y + rng.randint(-3, 3), z + rng.randint(0, 1)) for _ in range(n)]
        s = self.new()
        lib.shifter_buttons(s, 1, 0, 0)
        self.cmd(s, 0x24, 0x36, 3)
        self.run_frames(s, noisy(0, 0, 0x80))
        self.cmd(s, 0x24, 0x21, 9)
        self.run_frames(s, noisy(-300, -1500, 0x80))
        self.cmd(s, 0x24, 0x21, 10)
        for k, (x, y, z) in enumerate(GEARS, 1):
            self.run_frames(s, noisy(x, y, z))
            self.cmd(s, 0x24, 0x21, k)
        st = (ctypes.c_uint16 * 100)(*[0xFFFF] * 100)
        lib.shifter_save(s, st)
        s = self.new(list(st))
        lib.shifter_buttons(s, 1, 0, 0)
        self.run_frames(s, noisy(0, 0, 0x80))
        for k, (x, y, z) in enumerate(GEARS, 1):
            rows = self.run_frames(s, noisy(x + 120, y - 90, z, n=150))
            self.assertEqual(rows[-1][0], 1 << (k - 1), f'gear {k}')
            self.run_frames(s, noisy(0, 0, 0x80))
            self.assertEqual(self.report1(s)[1], 0, f'neutral after gear {k}')

    def test_raw_report(self):
        """Report 0x26 gives the reading before anti-jitter, at every level,
        and is sent while anti-jitter is on."""
        s = self.new()
        for level, wanted in ((0, 0), (1, 1), (3, 1), (0, 0)):
            self.cmd(s, 0x24, 0x36, level)
            self.assertEqual(bool(lib.shifter_raw_wanted(s)), bool(wanted), level)
        for level in range(4):
            s = self.calibrated(level)
            self.run_frames(s, [(120, -280, 0x80)] * 40)
            rows = self.run_frames(s, [(126, -274, 0x80)])
            out = ctypes.create_string_buffer(5)
            lib.shifter_report_raw(s, out)
            x, y = struct.unpack('>hh', out.raw[1:5])
            self.assertEqual(out.raw[0], 0x26)
            self.assertLessEqual(abs(x - 126) + abs(y + 274), 4, level)   # frame() rounds by 1 or 2
            if level >= 2:
                self.assertNotEqual(rows[0][2], (x, y), 'report 0x24 holds still')

    def test_a_bad_read_is_skipped(self):
        """One frame far from the last (seen on a unit: y 6598 at rest) moves
        nothing with anti-jitter on. Two in a row are a real jump: the second
        counts, one frame later than with none."""
        hold = [(-150, -850, 0x80)] * 60
        bad = [(1800, -3200, 0x80)]
        for level in range(4):
            s = self.calibrated(level)
            self.to_handbrake_mode(s)
            before = self.run_frames(s, hold)[-1]
            spike = self.run_frames(s, bad)[0]
            after = self.run_frames(s, hold[:5])
            if level == 0:
                self.assertNotEqual(spike[2], before[2], 'none passes the bad read')
            else:
                self.assertEqual((spike[1], spike[2]), (before[1], before[2]), level)
                self.assertEqual([r[1:3] for r in after], [before[1:3]] * 5, level)
            s = self.calibrated(level)
            self.to_handbrake_mode(s)
            self.run_frames(s, hold)
            jump = [r[1] for r in self.run_frames(s, [(-150, -1700, 0x80)] * 6)]
            full = jump.index(65535)
            ref = ref_full if level else full
            if not level:
                ref_full = full
            self.assertLessEqual(full, ref + 1, f'level {level}: one frame later at most')

    def test_starts_over_after_the_sensor_restarts(self):
        s, ref = self.calibrated(3), self.calibrated(0)
        for t in (s, ref):
            self.run_frames(t, [(100, -300, 0x80)] * 50)
            for _ in range(120):
                lib.shifter_sensor_frame(t, bytes(8))
        self.assertEqual(self.run_frames(s, [(110, -310, 0x80)])[0][2],
                         self.run_frames(ref, [(110, -310, 0x80)])[0][2], 'no value left from before')


class Saves(ctypes.Structure):
    _fields_ = [('changed_ms', ctypes.c_uint32), ('refill_ms', ctypes.c_uint32),
                ('pending', ctypes.c_uint8), ('allowance', ctypes.c_uint8)]


class SettingsWrites(unittest.TestCase):
    """When main.c writes the settings page (sh_saves_*)."""

    def setUp(self):
        self.assertEqual(ctypes.sizeof(Saves), lib.t_saves_sizeof())
        self.v = Saves()
        lib.sh_saves_init(ctypes.byref(self.v))

    def changed(self, t):
        lib.sh_saves_changed(ctypes.byref(self.v), t % 2**32)

    def take(self, t, urgent=0):
        return lib.sh_saves_take(ctypes.byref(self.v), t % 2**32, urgent)

    def write(self, t):
        """A change at t, written once it is due; returns the time of the write."""
        self.changed(t)
        t += SAVE_QUIET_MS
        while not self.take(t):
            t += 1000
        lib.sh_saves_spent(ctypes.byref(self.v))
        return t

    def test_nothing_to_write(self):
        self.assertEqual(self.take(0), 0)
        self.assertEqual(self.take(10**6, urgent=1), 0)

    def test_written_once_changes_stop(self):
        self.changed(0)
        self.changed(400)
        self.changed(900)
        self.assertEqual(self.take(900 + SAVE_QUIET_MS - 1), 0)
        self.assertEqual(self.take(900 + SAVE_QUIET_MS), 1)
        self.assertEqual(self.take(900 + SAVE_QUIET_MS + 1), 0, 'written twice')

    def test_urgent_write_skips_the_wait_and_the_limit(self):
        for _ in range(SAVE_BURST):
            lib.sh_saves_spent(ctypes.byref(self.v))
        self.changed(5)
        self.assertEqual(self.take(5), 0)
        self.assertEqual(self.take(5, urgent=1), 1)
        self.assertEqual(self.v.allowance, 0)

    def test_limit_after_a_burst(self):
        t = 0
        for _ in range(SAVE_BURST):
            t = self.write(t)
        self.assertEqual(self.v.allowance, 0)
        # The allowance started to drop with the first write, at SAVE_QUIET_MS.
        first = SAVE_QUIET_MS
        self.changed(t)
        self.assertEqual(self.take(first + SAVE_REFILL_MS - 1), 0)
        self.assertEqual(self.take(first + SAVE_REFILL_MS), 1)
        lib.sh_saves_spent(ctypes.byref(self.v))
        # From then on, one write per SAVE_REFILL_MS.
        t, times = first + SAVE_REFILL_MS, []
        for _ in range(3):
            t = self.write(t)
            times.append(t)
        self.assertEqual(times, [first + k * SAVE_REFILL_MS for k in (2, 3, 4)])

    def test_allowance_comes_back(self):
        t = 0
        for _ in range(SAVE_BURST):
            t = self.write(t)
        self.take(t + SAVE_BURST * SAVE_REFILL_MS)
        self.assertEqual(self.v.allowance, SAVE_BURST)
        # Time spent with a full allowance is not stored up for later.
        self.take(t + 10**7)
        t = self.write(t + 10**7)
        self.take(t + SAVE_REFILL_MS - 1)
        self.assertEqual(self.v.allowance, SAVE_BURST - 1)
        self.take(t + SAVE_REFILL_MS)
        self.assertEqual(self.v.allowance, SAVE_BURST)

    def test_clock_wraps(self):
        start = 2**32 - 300
        self.changed(start)
        self.assertEqual(self.take(start + SAVE_QUIET_MS - 1), 0)
        self.assertEqual(self.take(start + SAVE_QUIET_MS), 1)
        t = start
        for _ in range(SAVE_BURST):
            t = self.write(t)
        self.changed(t)
        self.assertEqual(self.take(start + SAVE_QUIET_MS + SAVE_REFILL_MS - 1), 0)
        self.assertEqual(self.take(start + SAVE_QUIET_MS + SAVE_REFILL_MS), 1)


if __name__ == '__main__':
    unittest.main()
