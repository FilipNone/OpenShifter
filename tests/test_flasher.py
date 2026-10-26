"""Tests of tools/openshifter_flash.py against a simulated shifter (no hardware)."""
import contextlib
import io
import os
import pathlib
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import openshifter_flash as fl  # noqa: E402

OPENSHIFTER_BIN = ROOT / 'firmware' / 'build' / 'openshifter.bin'
STOCK_BIN = pathlib.Path(os.environ.get('OPENSHIFTER_STOCK_FW',
                                        ROOT / 'firmware' / 'stock' / 'VelocityOneTuner_Shift_Firmware.bin'))
# Report descriptor read from a stock unit (10f5:7096).
STOCK_REPORT_DESCRIPTOR = bytes.fromhex(
    '05010904a1018501a10105091901290b15002501750195108102c00501a1010932150027ffff0000350047ffff'
    '0000751095018102c0a10185240901150026ff007508953f8106c0a10185240901150026ff007508953f9106c0c0')


def fake_image(n=1000, version=b'9.9.9', name=b'OpenShifter'):
    """A plausible application: vector table, then filler and an OpenShifter tag."""
    head = struct.pack('<4I', 0x20005000, fl.APP_BASE + 0x101, fl.APP_BASE + 0x103, fl.APP_BASE + 0x105)
    body = bytes((i * 7) & 0xFF for i in range(n - len(head)))
    tag = name + b' ' + version
    return head + body[:-len(tag)] + tag


def quiet():
    return contextlib.redirect_stdout(io.StringIO())


class Framing(unittest.TestCase):
    def test_commands_are_full_reports(self):
        for c in (fl.CMD_BOOT, fl.CMD_ERASE, fl.CMD_DONE):
            self.assertEqual(fl.command(c), bytes([0x24, c]) + bytes(62))

    def test_chunks_match_tuner(self):
        img = bytes(range(256)) * 2   # 512 bytes: 8 full chunks and one of 32
        self.assertEqual(fl.chunk_count(len(img)), 9)
        self.assertEqual(fl.chunk(img, 0), bytes([0x24, 0x02, 0, 0]) + img[:60])
        self.assertEqual(fl.chunk(img, 3), bytes([0x24, 0x02, 3, 0]) + img[180:240])
        last = fl.chunk(img, 8)
        self.assertEqual(last[:4 + 32], bytes([0x24, 0x02, 8, 0]) + img[480:])
        self.assertEqual(last[36:], b'\xff' * 28)
        self.assertEqual(fl.chunk(bytes(70000), 300)[2:4], struct.pack('<h', 300))
        self.assertTrue(all(len(fl.chunk(img, i)) == 64 for i in range(9)))


class Images(unittest.TestCase):
    def test_fake_image_is_valid(self):
        img = fl.Image(fake_image())
        self.assertEqual(img.problems(), [])
        self.assertEqual(img.openshifter_version, '9.9.9')

    def test_builds_from_before_the_rename(self):
        img = fl.Image(fake_image(name=b'OpenShift', version=b'0.5.0'))
        self.assertEqual((img.openshifter_version, img.mark), ('0.5.0', 'OpenShift 0.5.0'))
        self.assertTrue(img.known)

    @unittest.skipUnless(OPENSHIFTER_BIN.exists(), 'firmware not built')
    def test_openshifter_build(self):
        img = fl.Image(OPENSHIFTER_BIN.read_bytes())
        self.assertEqual(img.problems(), [])
        self.assertTrue(img.openshifter_version)

    @unittest.skipUnless(STOCK_BIN.exists(), 'stock image not available')
    def test_stock_image(self):
        img = fl.Image(STOCK_BIN.read_bytes())
        self.assertEqual(img.problems(), [])
        self.assertTrue(img.stock)
        self.assertEqual(fl.chunk_count(len(img.data)), 658)

    def test_rejects_bad_images(self):
        good = bytearray(fake_image())
        cases = {
            'tiny': bytes(10),
            'too big': bytes(good) + bytes(fl.APP_MAX),
            'stack outside RAM': struct.pack('<I', 0x08001000) + good[4:],
            'linked at 0x08000000': good[:4] + struct.pack('<I', 0x08000101) + good[8:],
            'ARM-mode reset vector': good[:4] + struct.pack('<I', fl.APP_BASE + 0x100) + good[8:],
            'ELF file': b'\x7fELF' + bytes(200),
        }
        for name, data in cases.items():
            self.assertTrue(fl.Image(data).problems(), name)

    def test_unknown_images_need_allow_foreign(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'other.bin')
            with open(p, 'wb') as f:
                f.write(fake_image().replace(b'OpenShifter', b'Something'))
            with quiet(), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(fl.main(['check', p]), 2)
                self.assertEqual(fl.main(['check', '--allow-foreign', p]), 0)
                self.assertEqual(fl.main(['check', p + '.elf']), 2)


class FlashSimulated(unittest.TestCase):
    def test_full_update_from_application_mode(self):
        img = fl.Image(fake_image(5000))
        sim = fl.SimShifter(b'old firmware')
        with quiet():
            app = fl.flash(sim, img)
        self.assertEqual(sim.violations, [])
        self.assertEqual(sim.application(len(img.data)), img.data)
        self.assertEqual(sim.flash[len(img.data):], b'\xff' * (fl.APP_MAX - len(img.data)))
        self.assertEqual(app.configuration, 'OpenShifter 9.9.9')
        cmds = [c for _, c in sim.log]
        n = fl.chunk_count(len(img.data))
        self.assertEqual(cmds, [fl.CMD_BOOT, fl.CMD_ERASE] + [fl.CMD_WRITE] * n + [fl.CMD_DONE])
        # Tuner's pacing: 2 s after erase, then >= 15 ms per chunk.
        t = [t for t, _ in sim.log]
        self.assertGreaterEqual(t[2] - t[1], fl.ERASE_WAIT_S)
        self.assertGreaterEqual(min(b - a for a, b in zip(t[2:], t[3:])), fl.CHUNK_GAP_S - 1e-9)

    def test_reinstalls_a_build_from_before_the_rename(self):
        img = fl.Image(fake_image(5000, b'0.4.0', b'OpenShift'))
        sim = fl.SimShifter(b'old firmware')
        with quiet():
            app = fl.flash(sim, img)
        self.assertEqual(app.configuration, 'OpenShift 0.4.0')
        self.assertEqual(app.firmware, 'OpenShift 0.4.0')

    def test_starts_directly_in_bootloader_mode(self):
        img = fl.Image(fake_image())
        sim = fl.SimShifter(boot_mode=True)
        with quiet():
            fl.flash(sim, img)
        self.assertEqual([c for _, c in sim.log][0], fl.CMD_ERASE)
        self.assertEqual(sim.application(len(img.data)), img.data)

    @unittest.skipUnless(STOCK_BIN.exists(), 'stock image not available')
    def test_restore_stock(self):
        img = fl.Image(STOCK_BIN.read_bytes())
        sim = fl.SimShifter(fake_image())
        with quiet():
            app = fl.flash(sim, img)
        self.assertEqual(sim.application(len(img.data)), img.data)
        self.assertEqual(app.firmware, 'stock (no OpenShifter identification)')

    def test_interrupted_update_can_be_retried(self):
        img = fl.Image(fake_image(3000))

        class Unplugged(fl.SimShifter):
            writes = 0

            def write(self, data):
                Unplugged.writes += 1
                if Unplugged.writes == 20:
                    raise fl.FlashError('write failed: No such device')
                super().write(data)
        sim = Unplugged()
        with quiet(), self.assertRaisesRegex(fl.FlashError, 'run the same command again'):
            fl.flash(sim, img)
        self.assertEqual(sim.pid, fl.PID_BOOT)
        with quiet():
            fl.flash(sim, img)
        self.assertEqual(sim.application(len(img.data)), img.data)

    def test_refuses_bootloader_without_report_0x24(self):
        class Odd(fl.SimShifter):
            def devices(self):
                d = super().devices()
                for x in d:
                    x.report_descriptor = bytes.fromhex('0600ff0901a101150026ff007508954081029102c0')
                return d
        sim = Odd(boot_mode=True)
        with quiet(), self.assertRaisesRegex(fl.FlashError, 'refusing to erase'):
            fl.flash(sim, fl.Image(fake_image()))
        self.assertEqual(sim.log, [])

    def test_reports_wrong_firmware_after_restart(self):
        class Stubborn(fl.SimShifter):
            def _reenumerate(self, pid):
                super()._reenumerate(pid)
                if pid == fl.PID_APP:
                    self.flash[:] = b'\xff' * fl.APP_MAX
        with quiet(), self.assertRaisesRegex(fl.FlashError, 'instead of'):
            fl.flash(Stubborn(), fl.Image(fake_image()))

    def test_no_device(self):
        class Absent(fl.SimShifter):
            def devices(self):
                return []
        with self.assertRaisesRegex(fl.FlashError, 'no VelocityOne'):
            fl.flash(Absent(), fl.Image(fake_image()))


class CommandLine(unittest.TestCase):
    def write_image(self, d, data):
        p = os.path.join(d, 'fw.bin')
        with open(p, 'wb') as f:
            f.write(data)
        return p

    def test_dry_run(self):
        with tempfile.TemporaryDirectory() as d, quiet() as out:
            p = self.write_image(d, fake_image())
            self.assertEqual(fl.main(['flash', '--dry-run', p], bus=fl.SimShifter()), 0)
        self.assertIn('dry run ok', out.getvalue())

    def test_confirmation_is_required(self):
        sim = fl.SimShifter()
        with tempfile.TemporaryDirectory() as d, quiet():
            p = self.write_image(d, fake_image())
            with mock.patch('builtins.input', return_value='y'):
                self.assertEqual(fl.main(['flash', p], bus=sim), 1)
            self.assertEqual(sim.log, [])
            with mock.patch('builtins.input', return_value='flash'):
                self.assertEqual(fl.main(['flash', p], bus=sim), 0)
        self.assertEqual(sim.application(1000), fake_image())


class Sysfs(unittest.TestCase):
    def make_tree(self, root, pid, configuration, rd=STOCK_REPORT_DESCRIPTOR, name='hidraw3'):
        usb = os.path.join(root, 'devices', 'pci0000:00', 'usb1', '1-4')
        hid = os.path.join(usb, '1-4:1.0', f'0003:10F5:{pid:04X}.0007')
        os.makedirs(hid)
        for p, v in ((os.path.join(usb, 'idVendor'), '10f5\n'), (os.path.join(usb, 'configuration'), configuration + '\n'),
                     (os.path.join(usb, 'serial'), 'TBRS004-20241224\n'),
                     (os.path.join(hid, 'uevent'), f'DRIVER=hid-generic\nHID_ID=0003:000010F5:0000{pid:04X}\n'
                                                   f'HID_NAME=VelocityOne Multi-Shift\n')):
            with open(p, 'w') as f:
                f.write(v)
        with open(os.path.join(hid, 'report_descriptor'), 'wb') as f:
            f.write(rd)
        cls = os.path.join(root, 'class', 'hidraw', name)
        os.makedirs(cls)
        os.symlink(hid, os.path.join(cls, 'device'))
        return os.path.join(root, 'class', 'hidraw')

    def test_finds_the_shifter(self):
        with tempfile.TemporaryDirectory() as d:
            bus = fl.LinuxBus(self.make_tree(d, 0x7096, 'OpenShifter 0.1.0'))
            (dev,) = bus.devices()
        self.assertEqual((dev.path, dev.pid, dev.mode), ('/dev/hidraw3', 0x7096, 'application'))
        self.assertEqual(dev.firmware, 'OpenShifter 0.1.0')
        self.assertEqual(dev.serial, 'TBRS004-20241224')
        self.assertTrue(dev.has_output_report(0x24))
        self.assertFalse(dev.has_output_report(0x01))

    def test_stock_and_bootloader(self):
        with tempfile.TemporaryDirectory() as d:
            (dev,) = fl.LinuxBus(self.make_tree(d, 0x7096, '')).devices()
            self.assertEqual(dev.firmware, 'stock (no OpenShifter identification)')
        with tempfile.TemporaryDirectory() as d:
            (dev,) = fl.LinuxBus(self.make_tree(d, 0x7097, '', rd=b'')).devices()
            self.assertEqual(dev.mode, 'bootloader')
            self.assertIsNone(dev.has_output_report())

    def test_ignores_other_devices(self):
        with tempfile.TemporaryDirectory() as d:
            root = self.make_tree(d, 0x7096, '')
            other = os.path.join(d, 'devices', 'kbd')
            os.makedirs(other)
            with open(os.path.join(other, 'uevent'), 'w') as f:
                f.write('HID_ID=0003:0000046D:0000C31C\n')
            os.makedirs(os.path.join(root, 'hidraw0'))
            os.symlink(other, os.path.join(root, 'hidraw0', 'device'))
            self.assertEqual([x.path for x in fl.LinuxBus(root).devices()], ['/dev/hidraw3'])


if __name__ == '__main__':
    unittest.main()
