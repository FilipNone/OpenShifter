"""Behavioural STM32F103/AIR32F103 model for running shifter firmware images.

Runs an unmodified application image (stock or OpenShift) under Unicorn with
models of the peripherals the shifter uses, so both images can be driven with
the same simulated magnet, buttons and USB host and their outputs compared.

Modelled: RCC (ready bits), FLASH controller (unlock, page erase, halfword
program with PGERR rules), GPIO A/B with pull-ups, a pin-level MLX90393-style
SPI sensor, TIM2..7 update interrupts, SysTick, NVIC/SCB with priorities and
nesting, DMA1 (instant memory->peripheral), USART1, TIM3 PWM capture for the
WS2812 chain, and the USB full-speed device peripheral (EPnR toggle/rc_w0
semantics, PMA, ISTR/CNTR) with a host that enumerates and polls it.

Not modelled: cycle timing (instructions per microsecond is a parameter),
analog behaviour, data toggles, the vendor bootloader itself (a reset request
ends the run and is reported).
"""
from __future__ import annotations

import collections
import struct

from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_MEM_WRITE, UC_HOOK_CODE
from unicorn.arm_const import (
    UC_ARM_REG_PC, UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PRIMASK, UC_ARM_REG_XPSR,
    UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4, UC_ARM_REG_R5,
    UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11,
    UC_ARM_REG_R12,
)

FLASH_BASE = 0x08000000
FLASH_SIZE = 0x20000
APP_BASE = 0x08010000
SETTINGS_ADDR = 0x0800F000
RAM_BASE = 0x20000000
RAM_SIZE = 0x10000
SYSMEM_BASE = 0x1FFFF000
MAGIC_RET = 0x0FFF0000
PERIPH_BASE = 0x40000000
PERIPH_SIZE = 0x30000

SAVED_REGS = [UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_R4,
              UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
              UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_R12, UC_ARM_REG_SP, UC_ARM_REG_LR,
              UC_ARM_REG_PC, UC_ARM_REG_XPSR]

USB_IRQ = 20
TIMERS = {0x40000000: 28, 0x40000400: 29, 0x40000800: 30, 0x40000C00: 50,
          0x40001000: 54, 0x40001400: 55}


class EmuError(Exception):
    pass


def u16(b, o):
    return b[o] | b[o + 1] << 8


# --------------------------------------------------------------------------
# Sensor
# --------------------------------------------------------------------------

class Sensor:
    """MLX90393-style SPI magnetometer, mode 3, MSB first, pin level."""

    def __init__(self):
        self.powered = False
        self.cs = 1
        self.sck = 1
        self.mosi = 1
        self.miso = 1
        self.regs = collections.defaultdict(int)
        self.measure = (0, 0, 0, 0)   # T, X, Y, Z (16-bit)
        self.burst = False
        self.log = []                 # (cmd bytes)
        self.rm_count = 0
        self._reset_xfer()

    def _reset_xfer(self):
        self.rx = []
        self.bit = 0
        self.byte = 0
        self.tx = collections.deque()
        self.tx_byte = None
        self.tx_bit = 0
        self.need = None

    def status(self):
        return 0x80 if self.burst else 0x00

    def _command_len(self, c):
        hi = c & 0xF0
        if hi == 0x50:
            return 2
        if hi == 0x60:
            return 4
        return 1

    def _execute(self):
        c = self.rx[0]
        hi = c & 0xF0
        self.log.append(bytes(self.rx))
        resp = [self.status()]
        if c == 0x80:
            self.burst = False
        elif hi == 0x10:
            self.burst = True
        elif hi == 0x40:
            self.rm_count += 1
            t, x, y, z = self.measure
            for bit, v in ((1, t), (2, x), (4, y), (8, z)):
                if c & bit:
                    resp += [(v >> 8) & 0xFF, v & 0xFF]
        elif hi == 0x50:
            v = self.regs[self.rx[1] >> 2]
            resp += [(v >> 8) & 0xFF, v & 0xFF]
        elif hi == 0x60:
            self.regs[self.rx[3] >> 2] = self.rx[1] << 8 | self.rx[2]
        self.tx.extend(resp)

    def pins(self, powered, cs, sck, mosi):
        if not powered:
            self.powered = False
            self.burst = False
            self._reset_xfer()
            self.cs, self.sck, self.mosi = cs, sck, mosi
            return
        self.powered = True
        if cs != self.cs:
            self.cs = cs
            self._reset_xfer()
        if cs == 0 and sck != self.sck:
            if sck == 0:   # falling: shift next response bit out
                if self.tx_byte is None or self.tx_bit == 8:
                    self.tx_byte = self.tx.popleft() if self.tx else None
                    self.tx_bit = 0
                if self.tx_byte is not None:
                    self.miso = (self.tx_byte >> (7 - self.tx_bit)) & 1
                    self.tx_bit += 1
                else:
                    self.miso = 1
            else:          # rising: sample MOSI
                if self.need is None or len(self.rx) < self.need:
                    self.byte = (self.byte << 1) | mosi
                    self.bit += 1
                    if self.bit == 8:
                        self.rx.append(self.byte & 0xFF)
                        self.bit = 0
                        self.byte = 0
                        if self.need is None:
                            self.need = self._command_len(self.rx[0])
                        if len(self.rx) == self.need:
                            self._execute()
        self.sck = sck
        self.mosi = mosi

    def drives_miso(self):
        return self.powered and self.cs == 0


# --------------------------------------------------------------------------
# USB device peripheral
# --------------------------------------------------------------------------

EP_CTR_RX, EP_DTOG_RX, EP_STAT_RX, EP_SETUP = 0x8000, 0x4000, 0x3000, 0x0800
EP_TYPE, EP_KIND, EP_CTR_TX, EP_DTOG_TX, EP_STAT_TX, EP_EA = 0x0600, 0x0100, 0x0080, 0x0040, 0x0030, 0x000F
STAT_DISABLED, STAT_STALL, STAT_NAK, STAT_VALID = 0, 1, 2, 3


class UsbDevice:
    def __init__(self, machine):
        self.m = machine
        self.epr = [0] * 8
        self.cntr = 0x0003
        self.flags = 0          # ISTR bits 8..14 (CTR is derived)
        self.fnr = 0
        self.daddr = 0
        self.btable = 0
        self.dppu = 0
        self.pma = bytearray(512)

    # register file
    def read(self, off):
        if off < 0x20:
            return self.epr[off >> 2]
        if off == 0x40:
            return self.cntr
        if off == 0x44:
            return self.istr()
        if off == 0x48:
            return self.fnr
        if off == 0x4C:
            return self.daddr
        if off == 0x50:
            return self.btable
        if off == 0x54:
            return self.dppu
        return 0

    def write(self, off, v):
        v &= 0xFFFF
        if off < 0x20:
            n = off >> 2
            old = self.epr[n]
            new = old & (EP_SETUP)
            new |= v & (EP_TYPE | EP_KIND | EP_EA)
            # rc_w0
            if v & EP_CTR_RX:
                new |= old & EP_CTR_RX
            if v & EP_CTR_TX:
                new |= old & EP_CTR_TX
            # toggles
            new |= (old ^ v) & (EP_DTOG_RX | EP_STAT_RX | EP_DTOG_TX | EP_STAT_TX)
            if not (new & EP_CTR_RX):
                new &= ~EP_SETUP
            self.epr[n] = new
        elif off == 0x40:
            if v & 1 and not self.cntr & 1:
                self.flags |= 0x0400   # FRES raises RESET
            self.cntr = v
        elif off == 0x44:
            self.flags &= (v | 0x80FF) & 0x7F00
        elif off == 0x4C:
            self.daddr = v & 0xFF
        elif off == 0x50:
            self.btable = v & 0xFFF8
        elif off == 0x54:
            self.dppu = v
        self.m.usb_level()

    def pma_read(self, off, size=2):
        # 512 bytes of packet memory, two bytes per 32-bit CPU word
        if off >= 0x400:
            return 0
        i = (off & ~3) >> 1
        w = self.pma[i] | self.pma[i + 1] << 8
        return (w >> (8 * (off & 3))) & ((1 << (8 * size)) - 1)

    def pma_write(self, off, v, size):
        if off >= 0x400:
            return
        i = (off & ~3) >> 1
        for k in range(size):
            b = (off & 3) + k
            if b < 2:
                self.pma[i + b] = (v >> (8 * k)) & 0xFF

    def istr(self):
        v = self.flags
        for n, r in enumerate(self.epr):
            if r & (EP_CTR_RX | EP_CTR_TX):
                v |= 0x8000 | n | (0x10 if r & EP_CTR_RX else 0)
                break
        return v

    def irq_level(self):
        return bool(self.istr() & self.cntr & 0xFF00)

    def attached(self):
        if self.cntr & 3:
            return False
        if self.m.profile.get('air_dppu', True) and not self.dppu:
            return False
        if self.m.gpio_out_low('A', 12):
            return False
        return True

    # buffer table
    def bt(self, ep, field):
        return self.pma_read((self.btable + 8 * ep + 2 * field) * 2)

    def bt_set(self, ep, field, v):
        self.pma_write((self.btable + 8 * ep + 2 * field) * 2, v, 2)

    def buf_read(self, addr, n):
        return bytes(self.pma[addr + i] for i in range(n))

    def buf_write(self, addr, data):
        for i, b in enumerate(data):
            self.pma[addr + i] = b

    def find_ep(self, num):
        for n, r in enumerate(self.epr):
            if (r & EP_EA) == num and (r & (EP_STAT_RX | EP_STAT_TX)) and (n or num == 0):
                return n
        return 0 if num == 0 else None

    def _stat(self, n, field_shift):
        return (self.epr[n] >> field_shift) & 3

    def _set_stat_rx(self, n, s):
        self.epr[n] = (self.epr[n] & ~EP_STAT_RX) | s << 12

    def _set_stat_tx(self, n, s):
        self.epr[n] = (self.epr[n] & ~EP_STAT_TX) | s << 4

    # host tokens
    def addressed(self, addr):
        return (self.daddr & 0x80) and (self.daddr & 0x7F) == addr

    def setup(self, addr, data):
        if not self.addressed(addr):
            return 'TIMEOUT'
        n = self.find_ep(0)
        if (self.epr[n] & EP_TYPE) != 0x0200 or self._stat(n, 12) == STAT_DISABLED:
            return 'TIMEOUT'
        self.buf_write(self.bt(n, 2), data)
        self.bt_set(n, 3, (self.bt(n, 3) & 0xFC00) | len(data))
        self.epr[n] |= EP_CTR_RX | EP_SETUP
        self._set_stat_rx(n, STAT_NAK)
        self._set_stat_tx(n, STAT_NAK)
        self.m.usb_level()
        return 'ACK'

    def in_token(self, addr, ep):
        if not self.addressed(addr):
            return 'TIMEOUT'
        n = self.find_ep(ep)
        if n is None:
            return 'TIMEOUT'
        s = self._stat(n, 4)
        if s == STAT_VALID:
            cnt = self.bt(n, 1) & 0x3FF
            data = self.buf_read(self.bt(n, 0), cnt)
            self._set_stat_tx(n, STAT_NAK)
            self.epr[n] |= EP_CTR_TX
            self.epr[n] ^= EP_DTOG_TX
            self.m.usb_level()
            return data
        return {STAT_NAK: 'NAK', STAT_STALL: 'STALL'}.get(s, 'TIMEOUT')

    def out_token(self, addr, ep, data):
        if not self.addressed(addr):
            return 'TIMEOUT'
        n = self.find_ep(ep)
        if n is None:
            return 'TIMEOUT'
        s = self._stat(n, 12)
        if s == STAT_VALID:
            c = self.bt(n, 3)
            size = ((c >> 10) & 0x1F) * 2 if not c & 0x8000 else (((c >> 10) & 0x1F) + 1) * 32
            if len(data) > size:
                raise EmuError(f'babble: {len(data)} bytes into {size}-byte EP{ep} buffer')
            self.buf_write(self.bt(n, 2), data)
            self.bt_set(n, 3, (c & 0xFC00) | len(data))
            self.epr[n] = (self.epr[n] | EP_CTR_RX) & ~EP_SETUP
            self._set_stat_rx(n, STAT_NAK)
            self.epr[n] ^= EP_DTOG_RX
            self.m.usb_level()
            return 'ACK'
        return {STAT_NAK: 'NAK', STAT_STALL: 'STALL'}.get(s, 'TIMEOUT')

    def bus_reset(self):
        self.daddr = 0
        for n in range(8):
            self.epr[n] = 0
        self.flags |= 0x0400
        self.m.usb_level()

    def suspend(self):
        self.flags |= 0x0800
        self.m.usb_level()

    def resume(self):
        self.flags |= 0x1000
        self.m.usb_level()

    def sof(self):
        self.fnr = (self.fnr + 1) & 0x7FF
        self.flags |= 0x0200
        self.m.usb_level()


class UsbHost:
    """Enumerates the device like a PC host, then polls EP1 IN every frame."""

    def __init__(self, machine):
        self.m = machine
        self.dev = machine.usb
        self.addr = 0
        self.task = None
        self.queue = collections.deque()
        self.attached_since = None
        self.state = 'detached'
        self.config = None
        self.descriptors = {}
        self.in_reports = []
        self.poll = True
        self.log = []
        self.auto_enumerate = True

    def run_task(self, gen):
        self.queue.append(gen)

    def tick_frame(self):
        """Once per simulated millisecond."""
        dev = self.dev
        if not dev.attached():
            if self.state != 'detached':
                self.log.append((self.m.time_us, 'detach'))
            self.state = 'detached'
            self.attached_since = None
            self.config = None
            self.addr = 0
            return
        if self.state == 'detached':
            self.state = 'attached'
            self.attached_since = self.m.time_us
            self.log.append((self.m.time_us, 'attach'))
        if self.state == 'attached' and self.m.time_us - self.attached_since >= 100_000 and self.auto_enumerate:
            self.state = 'enumerating'
            self.run_task(self.enumerate())
        if self.state != 'suspended':
            dev.sof()
        if self.config and self.poll and self.state == 'configured' and not self.task:
            r = dev.in_token(self.addr, 1)
            if isinstance(r, bytes):
                self.in_reports.append((self.m.time_us, r))

    def tick_slice(self):
        if self.task is None and self.queue:
            self.task = self.queue.popleft()
        if self.task is not None:
            try:
                next(self.task)
            except StopIteration:
                self.task = None

    # --- transaction helpers (generators; each yield lets the CPU run) ---
    def _wait(self, n=1):
        for _ in range(n):
            yield

    def _token(self, fn, *args, limit=20000):
        for _ in range(limit):
            r = fn(*args)
            if r != 'NAK':
                yield
                return r
            yield
        raise EmuError(f'{fn.__name__}{args[:2]} NAKed for {limit} slices')

    def control(self, setup, data_out=None):
        bm, req, val, idx, length = struct.unpack('<BBHHH', setup)
        r = self.dev.setup(self.addr, setup)
        if r != 'ACK':
            raise EmuError(f'SETUP not acknowledged: {r}')
        yield
        if bm & 0x80:
            data = b''
            while True:
                pkt = yield from self._token(self.dev.in_token, self.addr, 0)
                if pkt == 'STALL':
                    return 'STALL'
                if not isinstance(pkt, bytes):
                    raise EmuError(f'IN data stage: {pkt}')
                data += pkt
                if len(pkt) < 64 or len(data) >= length:
                    break
            r = yield from self._token(self.dev.out_token, self.addr, 0, b'')
            if r != 'ACK':
                raise EmuError(f'status OUT: {r}')
            return data
        if length:
            data_out = data_out or b''
            for i in range(0, len(data_out), 64):
                r = yield from self._token(self.dev.out_token, self.addr, 0, data_out[i:i + 64])
                if r == 'STALL':
                    return 'STALL'
                if r != 'ACK':
                    raise EmuError(f'OUT data stage: {r}')
        r = yield from self._token(self.dev.in_token, self.addr, 0)
        if r == 'STALL':
            return 'STALL'
        if r != b'':
            raise EmuError(f'status IN: {r!r}')
        return b''

    def get_descriptor(self, dtype, index, length, lang=0, recipient=0x80):
        return (yield from self.control(struct.pack('<BBHHH', recipient, 6, dtype << 8 | index, lang, length)))

    def enumerate(self):
        dev = self.dev
        dev.bus_reset()
        self.addr = 0
        yield from self._wait(200)
        d8 = yield from self.get_descriptor(1, 0, 64)
        dev.bus_reset()
        yield from self._wait(200)
        r = yield from self.control(struct.pack('<BBHHH', 0, 5, 7, 0, 0))
        if r != b'':
            raise EmuError(f'SET_ADDRESS: {r}')
        self.addr = 7
        yield from self._wait(40)
        dd = yield from self.get_descriptor(1, 0, 18)
        c9 = yield from self.get_descriptor(2, 0, 9)
        total = struct.unpack_from('<H', c9, 2)[0]
        cd = yield from self.get_descriptor(2, 0, total)
        strings = {}
        s0 = yield from self.get_descriptor(3, 0, 255)
        for i in (dd[14], dd[15], dd[16], cd[6]):
            if i:
                s = yield from self.get_descriptor(3, i, 255, 0x0409)
                strings[i] = s[2:].decode('utf-16le') if isinstance(s, bytes) else s
        r = yield from self.control(struct.pack('<BBHHH', 0, 9, cd[5], 0, 0))
        if r != b'':
            raise EmuError(f'SET_CONFIGURATION: {r}')
        self.config = cd[5]
        r = yield from self.control(struct.pack('<BBHHH', 0x21, 0x0A, 0, 0, 0))   # SET_IDLE
        rd = yield from self.get_descriptor(0x22, 0, 512, 0, 0x81)
        self.descriptors = {'dev8': d8, 'device': dd, 'config': cd, 'lang': s0, 'strings': strings,
                            'report': rd, 'set_idle': r}
        self.state = 'configured'
        self.log.append((self.m.time_us, 'configured'))

    def send_out_report(self, data):
        """Interrupt OUT on EP1, as hidraw/hidapi do."""
        def g():
            r = yield from self._token(self.dev.out_token, self.addr, 1, data)
            if r != 'ACK':
                raise EmuError(f'EP1 OUT: {r}')
        self.run_task(g())

    def set_report(self, data):
        def g():
            r = yield from self.control(struct.pack('<BBHHH', 0x21, 0x09, 0x0200 | data[0], 0, len(data)), data)
            self.last_control = r
        self.run_task(g())

    def get_report(self, report_id, length):
        def g():
            r = yield from self.control(struct.pack('<BBHHH', 0xA1, 0x01, 0x0100 | report_id, 0, length))
            self.last_control = r
        self.run_task(g())


# --------------------------------------------------------------------------
# Machine
# --------------------------------------------------------------------------

class Machine:
    def __init__(self, image, settings=None, *, mips=24, slice_us=20, page_size=2048,
                 hse=True, profile=None, uid_top=0x1C):
        self.profile = dict(profile or {})
        self.mips = mips
        self.slice_us = slice_us
        self.page_size = page_size
        self.hse = hse
        self.time_us = 0
        self.uc = uc = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        uc.mem_map(FLASH_BASE, FLASH_SIZE)
        uc.mem_write(FLASH_BASE, b'\xff' * FLASH_SIZE)
        uc.mem_write(APP_BASE, bytes(image))
        if settings is not None:
            uc.mem_write(SETTINGS_ADDR, bytes(settings))
        uc.mem_map(RAM_BASE, RAM_SIZE)
        # AIR32 ROM services table at 0x1FFFD000 (stock calls entry +0xC while
        # configuring the PLL); each entry is a logged 'bx lr' stub.
        uc.mem_map(0x1FFFD000, 0x3000)
        for i in range(16):
            uc.mem_write(0x1FFFD000 + 4 * i, struct.pack('<I', (0x1FFFD100 + 4 * i) | 1))
            uc.mem_write(0x1FFFD100 + 4 * i, b'\x70\x47\x00\xbf')
        uc.hook_add(UC_HOOK_CODE, self._rom_hook, begin=0x1FFFD100, end=0x1FFFD13F)
        uc.mem_write(SYSMEM_BASE + 0x7E0, struct.pack('<H', 128))
        uc.mem_write(SYSMEM_BASE + 0x7E8, struct.pack('<III', uid_top << 24 | 0x123456, 0x89ABCDEF, 0x01020304))
        uc.mem_map(MAGIC_RET, 0x1000)
        uc.mmio_map(PERIPH_BASE, PERIPH_SIZE, self._p_read, None, self._p_write, None)
        uc.mmio_map(0xE000E000, 0x1000, self._ppb_read, None, self._ppb_write, None)
        uc.mmio_map(0x42000000, 0x2000000, self._bb_read, None, self._bb_write, None)
        uc.mmio_map(0x22000000, 0x200000, self._sbb_read, None, self._sbb_write, None)
        uc.hook_add(UC_HOOK_MEM_WRITE, self._flash_write_hook, begin=FLASH_BASE, end=FLASH_BASE + FLASH_SIZE - 1)

        self.regs = collections.defaultdict(int)
        self.regs[0x40021000] = 0x83          # RCC_CR: HSION, HSIRDY
        self.regs[0x40022010] = 0x80          # FLASH_CR: LOCK
        for port in (0x40010800, 0x40010C00):
            self.regs[port] = self.regs[port + 4] = 0x44444444
        self.flash_keys = 0
        self.flash_events = []
        self.flash_violations = []
        self.sensor = Sensor()
        self.buttons = {'PA4': False, 'PA5': False, 'PB10': False}
        self.usb = UsbDevice(self)
        self.host = UsbHost(self)
        self.uart = bytearray()
        self.uart_packets = []
        self.led_frames = []
        self.events = []
        self.reset_requested = False
        self.stopped_reason = None

        # exceptions
        self.pending = set()
        self.nvic_enabled = set()
        self.ipr = collections.defaultdict(int)
        self.shpr = collections.defaultdict(int)
        self.active = []                       # (exc, saved regs)
        self.vtor = 0
        self.prigroup = 0
        self.systick = {'csr': 0, 'rvr': 0, 'acc': 0}
        self.timer_acc = collections.defaultdict(float)

        sp, pc = struct.unpack('<II', bytes(image[:8]))
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_PC, pc)
        self.pc = pc
        self.vector_base = APP_BASE

    def _rom_hook(self, uc, addr, size, ud):
        self.events.append(('rom_call', (addr - 0x1FFFD100) // 4,
                            [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)]))

    # ---------------- memory helpers ----------------
    def read32(self, a):
        return struct.unpack('<I', self.uc.mem_read(a, 4))[0]

    def read16(self, a):
        return struct.unpack('<H', self.uc.mem_read(a, 2))[0]

    def settings(self, n=100):
        return list(struct.unpack(f'<{n}H', self.uc.mem_read(SETTINGS_ADDR, 2 * n)))

    def flash_bytes(self, addr, n):
        return bytes(self.uc.mem_read(addr, n))

    # ---------------- flash ----------------
    def _flash_write_hook(self, uc, access, addr, size, value, ud):
        cr = self.regs[0x40022010]
        old = struct.unpack('<H', uc.mem_read(addr & ~1, 2))[0]
        ok = (cr & 1) and not (cr & 0x80) and size == 2 and not addr & 1 and (old == 0xFFFF or value == 0)
        if addr < SETTINGS_ADDR:
            ok = False
            self.flash_violations.append(('bootloader-region', hex(addr), hex(value)))
        if not ok:
            self.flash_violations.append(('program', hex(addr), size, hex(old), hex(value), hex(cr)))
            self.regs[0x4002200C] |= 0x04
            # undo after the store lands
            self._undo = (addr & ~1, old)
        else:
            self.flash_events.append(('program', addr, value))
            self.regs[0x4002200C] |= 0x20

    def _flash_ctrl_write(self, a, v):
        if a == 0x40022004:   # KEYR
            if self.flash_keys == 0 and v == 0x45670123:
                self.flash_keys = 1
            elif self.flash_keys == 1 and v == 0xCDEF89AB:
                self.flash_keys = 0
                self.regs[0x40022010] &= ~0x80
            else:
                self.flash_keys = 0
            return
        if a == 0x4002200C:   # SR: write 1 to clear
            self.regs[a] &= ~(v & 0x34)
            return
        if a == 0x40022010:
            if self.regs[a] & 0x80:
                return            # locked: ignored
            self.regs[a] = v
            if v & 0x40 and v & 0x2:   # PER + STRT
                page = self.regs[0x40022014] & ~(self.page_size - 1)
                if page < SETTINGS_ADDR or page >= FLASH_BASE + FLASH_SIZE:
                    self.flash_violations.append(('erase', hex(page)))
                    self.regs[0x4002200C] |= 0x10
                else:
                    self.uc.mem_write(page, b'\xff' * self.page_size)
                    self.flash_events.append(('erase', page))
                    self.regs[0x4002200C] |= 0x20
                self.regs[a] &= ~0x40
            if v & 0x40 and v & 0x4:
                self.flash_violations.append(('mass-erase',))
            return
        self.regs[a] = v

    # ---------------- GPIO ----------------
    PORTS = {'A': 0x40010800, 'B': 0x40010C00}

    def _cfg(self, port, pin):
        base = self.PORTS[port]
        cr = self.regs[base + (4 if pin >= 8 else 0)]
        return (cr >> ((pin & 7) * 4)) & 0xF

    def gpio_out_low(self, port, pin):
        cfg = self._cfg(port, pin)
        return (cfg & 3) and not (cfg & 8) and not (self.regs[self.PORTS[port] + 0xC] >> pin) & 1

    def _idr(self, port):
        base = self.PORTS[port]
        odr = self.regs[base + 0xC]
        v = 0
        for pin in range(16):
            cfg = self._cfg(port, pin)
            o = (odr >> pin) & 1
            if cfg & 3:                      # output
                bit = o
            else:
                ext = None
                name = f'P{port}{pin}'
                if name in self.buttons and self.buttons[name]:
                    ext = 0
                if port == 'B' and pin == 14 and self.sensor.drives_miso():
                    ext = self.sensor.miso
                if ext is not None:
                    bit = ext
                elif (cfg >> 2) == 2:        # pull up/down
                    bit = o
                else:
                    bit = 1
            v |= bit << pin
        return v

    def _gpio_changed(self):
        a = self.regs[0x40010800 + 0xC]
        b = self.regs[0x40010C00 + 0xC]
        pa7_out = self._cfg('A', 7) & 3
        powered = bool(pa7_out) and not (a >> 7) & 1
        self.sensor.pins(powered, (b >> 12) & 1, (b >> 13) & 1, (b >> 15) & 1)

    # ---------------- DMA ----------------
    def _dma_run(self, ch):
        base = 0x40020008 + 20 * (ch - 1)
        ccr = self.regs[base]
        n = self.regs[base + 4] & 0xFFFF
        if not (ccr & 1) or n == 0 or not (ccr & 0x10):
            return
        par, mar = self.regs[base + 8], self.regs[base + 12]
        msize = 1 << ((ccr >> 10) & 3)
        data = []
        for i in range(n):
            a = mar + (i * msize if ccr & 0x80 else 0)
            data.append(int.from_bytes(self.uc.mem_read(a, msize), 'little'))
        if par == 0x40013804:
            self.uart += bytes(x & 0xFF for x in data)
            self.uart_packets.append((self.time_us, bytes(x & 0xFF for x in data)))
        elif par == 0x4000043C:
            self._led_capture(data)
        else:
            self.events.append(('dma', ch, hex(par), n))
        self.regs[base + 4] = 0
        self.regs[0x40020000] |= 0x3 << (4 * (ch - 1))   # GIF + TCIF
        if ccr & 0x2:
            self.pending.add(11 + ch - 1)

    def _led_capture(self, values):
        arr = self.regs[0x4000042C] + 1
        bits = [1 if v * 2 > arr else 0 for v in values if v]
        out = bytes(int(''.join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits) - 7, 8))
        self.led_frames.append((self.time_us, out))

    # ---------------- peripheral bus ----------------
    def _p_read(self, uc, off, size, ud):
        a = PERIPH_BASE + off
        if 0x40005C00 <= a < 0x40005C60:
            return self.usb.read(a - 0x40005C00)
        if 0x40006000 <= a < 0x40006400:
            return self.usb.pma_read(a - 0x40006000, size)
        wa = a & ~3
        sh = (a & 3) * 8
        if wa == 0x40021000:
            v = self.regs[wa]
            v |= (v & 1) << 1
            if self.hse:
                v |= (v & (1 << 16)) << 1
            v |= (v & (1 << 24)) << 1
        elif wa == 0x40021004:
            v = self.regs[wa]
            v = (v & ~0xC) | (v & 3) << 2
        elif wa == 0x40021024:
            v = self.regs[wa] | 0x2   # LSIRDY
        elif wa in (0x40010808, 0x40010C08):
            v = self._idr('A' if wa == 0x40010808 else 'B')
        elif wa == 0x40013800:
            v = 0xC0                  # TXE | TC
        else:
            v = self.regs[wa]
        return (v >> sh) & ((1 << (8 * size)) - 1)

    def _p_write(self, uc, off, size, value, ud):
        a = PERIPH_BASE + off
        if 0x40005C00 <= a < 0x40005C60:
            self.usb.write(a - 0x40005C00, value)
            return
        if 0x40006000 <= a < 0x40006400:
            self.usb.pma_write(a - 0x40006000, value, size)
            return
        wa = a & ~3
        sh = (a & 3) * 8
        if size < 4:
            mask = ((1 << (8 * size)) - 1) << sh
            value = (self.regs[wa] & ~mask) | ((value << sh) & mask)
        self._reg_write(wa, value & 0xFFFFFFFF)

    def _reg_write(self, a, v):
        if 0x40022000 <= a < 0x40022400:
            self._flash_ctrl_write(a, v)
            return
        for port in (0x40010800, 0x40010C00):
            if a == port + 0x10:      # BSRR
                odr = self.regs[port + 0xC]
                odr &= ~(v >> 16)
                odr |= v & 0xFFFF
                self.regs[port + 0xC] = odr & 0xFFFF
                self._gpio_changed()
                return
            if a == port + 0x14:      # BRR
                self.regs[port + 0xC] &= ~v & 0xFFFF
                self._gpio_changed()
                return
            if a in (port, port + 4, port + 0xC):
                self.regs[a] = v
                self._gpio_changed()
                return
        if a == 0x40013804:
            self.uart.append(v & 0xFF)
            return
        if a == 0x40020004:           # DMA IFCR
            self.regs[0x40020000] &= ~v
            return
        if 0x40020008 <= a < 0x40020008 + 20 * 7:
            self.regs[a] = v
            ch = (a - 0x40020008) // 20 + 1
            self._dma_run(ch)
            return
        if a in TIMERS or any(t <= a < t + 0x400 for t in TIMERS):
            base = a & ~0x3FF
            if a == base + 0x10:     # SR: rc_w0
                self.regs[a] &= v
                return
            if a == base + 0x14:     # EGR
                if v & 1:
                    self.timer_acc[base] = 0
                return
        self.regs[a] = v

    # bit-band
    def _bb_read(self, uc, off, size, ud):
        a = PERIPH_BASE + (off >> 5)
        bit = (off >> 2) & 7
        return (self._p_read(uc, a - PERIPH_BASE, 1, None) >> bit) & 1

    def _bb_write(self, uc, off, size, value, ud):
        a = PERIPH_BASE + (off >> 5)
        bit = (off >> 2) & 7
        cur = self._p_read(uc, a - PERIPH_BASE, 1, None)
        cur = (cur | 1 << bit) if value & 1 else (cur & ~(1 << bit))
        self._p_write(uc, a - PERIPH_BASE, 1, cur, None)

    def _sbb_read(self, uc, off, size, ud):
        a = RAM_BASE + (off >> 5)
        return (uc.mem_read(a, 1)[0] >> ((off >> 2) & 7)) & 1

    def _sbb_write(self, uc, off, size, value, ud):
        a = RAM_BASE + (off >> 5)
        bit = (off >> 2) & 7
        cur = uc.mem_read(a, 1)[0]
        cur = (cur | 1 << bit) if value & 1 else (cur & ~(1 << bit))
        uc.mem_write(a, bytes([cur]))

    # ---------------- system control space ----------------
    def _ppb_read(self, uc, off, size, ud):
        a = 0xE000E000 + off
        wa = a & ~3
        sh = (a & 3) * 8
        if wa == 0xE000E010:
            v = self.systick['csr'] | (0x10000 if self.systick.get('flag') else 0)
            self.systick['flag'] = False
        elif wa == 0xE000E014:
            v = self.systick['rvr']
        elif wa == 0xE000E018:
            st = self.systick
            v = (st['rvr'] - int(st['acc'])) % (st['rvr'] + 1) if st['rvr'] else 0
        elif 0xE000E100 <= wa < 0xE000E120:
            i = (wa - 0xE000E100) * 8
            v = sum(1 << b for b in range(32) if i + b in self.nvic_enabled)
        elif 0xE000E200 <= wa < 0xE000E220 or 0xE000E280 <= wa < 0xE000E2A0:
            i = ((wa - 0xE000E200) & 0x7F) * 8
            v = sum(1 << b for b in range(32) if (16 + i + b) in self.pending)
        elif 0xE000E400 <= wa < 0xE000E450:
            n = wa - 0xE000E400
            v = sum(self.ipr[n + k] << (8 * k) for k in range(4))
        elif wa == 0xE000ED00:
            v = 0x412FC231
        elif wa == 0xE000ED04:
            v = (1 << 28 if 14 in self.pending else 0) | (1 << 26 if 15 in self.pending else 0)
            if self.active:
                v |= self.active[-1][0]
        elif wa == 0xE000ED08:
            v = self.vtor
        elif wa == 0xE000ED0C:
            v = 0xFA050000 | self.prigroup << 8
        elif 0xE000ED18 <= wa < 0xE000ED24:
            n = wa - 0xE000ED18 + 4
            v = sum(self.shpr[n + k] << (8 * k) for k in range(4))
        else:
            v = self.regs[wa]
        return (v >> sh) & ((1 << (8 * size)) - 1)

    def _ppb_write(self, uc, off, size, value, ud):
        a = 0xE000E000 + off
        if 0xE000E400 <= a < 0xE000E450:
            for k in range(size):
                self.ipr[a - 0xE000E400 + k] = (value >> (8 * k)) & 0xF0
            return
        if 0xE000ED18 <= a < 0xE000ED24:
            for k in range(size):
                self.shpr[a - 0xE000ED18 + 4 + k] = (value >> (8 * k)) & 0xF0
            return
        wa = a & ~3
        if wa == 0xE000E010:
            self.systick['csr'] = value & 7
        elif wa == 0xE000E014:
            self.systick['rvr'] = value & 0xFFFFFF
        elif wa == 0xE000E018:
            self.systick['acc'] = 0
            self.systick['flag'] = False
        elif 0xE000E100 <= wa < 0xE000E120:
            i = (wa - 0xE000E100) * 8
            self.nvic_enabled |= {i + b for b in range(32) if value >> b & 1}
        elif 0xE000E180 <= wa < 0xE000E1A0:
            i = (wa - 0xE000E180) * 8
            self.nvic_enabled -= {i + b for b in range(32) if value >> b & 1}
        elif 0xE000E200 <= wa < 0xE000E220:
            i = (wa - 0xE000E200) * 8
            self.pending |= {16 + i + b for b in range(32) if value >> b & 1}
        elif 0xE000E280 <= wa < 0xE000E2A0:
            i = (wa - 0xE000E280) * 8
            self.pending -= {16 + i + b for b in range(32) if value >> b & 1}
        elif wa == 0xE000ED04:
            if value & (1 << 28):
                self.pending.add(14)
            if value & (1 << 27):
                self.pending.discard(14)
            if value & (1 << 26):
                self.pending.add(15)
            if value & (1 << 25):
                self.pending.discard(15)
        elif wa == 0xE000ED08:
            self.vtor = value
            self.vector_base = value
        elif wa == 0xE000ED0C:
            if (value >> 16) == 0x05FA:
                self.prigroup = (value >> 8) & 7
                if value & 4:
                    self.reset_requested = True
                    self.events.append(('reset', self.time_us))
                    uc.emu_stop()
        else:
            self.regs[wa] = value

    # ---------------- interrupts ----------------
    def usb_level(self):
        if self.usb.irq_level():
            self.pending.add(16 + USB_IRQ)

    def _prio(self, exc):
        if exc < 4:
            return -1
        if exc >= 16:
            return self.ipr[exc - 16]
        return self.shpr[exc]

    def _group(self, p):
        if p < 0:
            return p
        return p & ((0xFF << (self.prigroup + 1)) & 0xFF)

    def _exec_prio(self):
        p = 256
        for exc, _ in self.active:
            p = min(p, self._group(self._prio(exc)))
        if self.uc.reg_read(UC_ARM_REG_PRIMASK) & 1:
            p = min(p, 0)
        return p

    def _take_exception(self):
        cur = self._exec_prio()
        best = None
        for exc in self.pending:
            if exc >= 16 and (exc - 16) not in self.nvic_enabled:
                continue
            if exc == 15 and not self.systick['csr'] & 2:
                continue
            g = self._group(self._prio(exc))
            if g < cur and (best is None or (g, self._prio(exc), exc) < best[0]):
                best = ((g, self._prio(exc), exc), exc)
        if best is None:
            return False
        exc = best[1]
        self.pending.discard(exc)
        uc = self.uc
        saved = [uc.reg_read(r) for r in SAVED_REGS]
        self.active.append((exc, saved))
        handler = self.read32(self.vector_base + 4 * exc)
        if handler in (0, 0xFFFFFFFF):
            raise EmuError(f'no handler for exception {exc}')
        sp = (saved[13] - 64) & ~7
        uc.reg_write(UC_ARM_REG_SP, sp)
        uc.reg_write(UC_ARM_REG_LR, MAGIC_RET | 1)
        self.pc = handler | 1
        self.events.append(('exc', exc)) if exc < 14 else None
        return True

    def _return_exception(self):
        exc, saved = self.active.pop()
        for r, v in zip(SAVED_REGS, saved):
            self.uc.reg_write(r, v)
        self.pc = saved[15] | 1
        if exc == 16 + USB_IRQ:
            self.usb_level()

    def sysclk_mhz(self):
        cfgr = self.regs[0x40021004]
        sw = cfgr & 3
        if sw == 1:
            return 8 if self.hse else 8
        if sw == 2:
            src = (8 / (2 if cfgr & (1 << 17) else 1)) if cfgr & (1 << 16) else 4
            mul = ((cfgr >> 18) & 0xF) | ((cfgr >> 28) & 1) << 4
            mul = 16 if mul == 0xF else mul + 2
            return src * mul
        return 8

    def timer_mhz(self):
        cfgr = self.regs[0x40021004]
        h = self.sysclk_mhz()
        ppre1 = (cfgr >> 8) & 7
        div = 1 << (ppre1 - 3) if ppre1 >= 4 else 1
        return h / div * (2 if div > 1 else 1)

    def _advance_time(self, dt):
        self.time_us += dt
        st = self.systick
        hclk = self.sysclk_mhz()
        if st['csr'] & 1 and st['rvr']:
            st['acc'] += dt * (hclk if st['csr'] & 4 else hclk / 8)
            period = st['rvr'] + 1
            while st['acc'] >= period:
                st['acc'] -= period
                st['flag'] = True
                if st['csr'] & 2:
                    self.pending.add(15)
        tmhz = self.timer_mhz()
        for base, irq in TIMERS.items():
            cr1 = self.regs[base]
            if not cr1 & 1:
                continue
            period = (self.regs[base + 0x28] + 1) * ((self.regs[base + 0x2C] & 0xFFFF) + 1)
            self.timer_acc[base] += dt * tmhz
            while self.timer_acc[base] >= period:
                self.timer_acc[base] -= period
                self.regs[base + 0x10] |= 1
                if self.regs[base + 0x0C] & 1:
                    self.pending.add(16 + irq)
        self._frame_acc = getattr(self, '_frame_acc', 0) + dt
        while self._frame_acc >= 1000:
            self._frame_acc -= 1000
            self.host.tick_frame()

    # ---------------- run loop ----------------
    def run(self, ms=None, until=None, max_ms=None):
        """Run for `ms` simulated milliseconds, or until `until()` is true."""
        end = self.time_us + int(ms * 1000) if ms is not None else None
        limit = self.time_us + int((max_ms or 60_000) * 1000)
        uc = self.uc
        budget = self.mips * self.slice_us
        while True:
            if self.reset_requested:
                self.stopped_reason = 'reset'
                return 'reset'
            if end is not None and self.time_us >= end:
                return 'time'
            if until is not None and until():
                return 'until'
            if self.time_us >= limit:
                raise EmuError(f'timeout at {self.time_us / 1000:.1f} ms, pc={self.pc:#x}')
            try:
                uc.emu_start(self.pc | 1, MAGIC_RET, count=budget)
            except UcError as e:
                pc = uc.reg_read(UC_ARM_REG_PC)
                raise EmuError(f'CPU error {e} at pc={pc:#x} t={self.time_us}us') from None
            undo = getattr(self, '_undo', None)
            if undo:
                uc.mem_write(undo[0], struct.pack('<H', undo[1]))
                self._undo = None
            self.pc = uc.reg_read(UC_ARM_REG_PC)
            if (self.pc & ~1) == MAGIC_RET:
                self._return_exception()
                continue
            self._advance_time(self.slice_us)
            self.host.tick_slice()
            while self._take_exception():
                break

    # ---------------- convenience ----------------
    def set_position(self, x, y, z_hi=0x80, t=0x8000):
        """Place the magnet so the firmware computes roughly (x, y)."""
        import math
        DEG, RAD = 182.04444444444445, 57.32484076433121
        CG, K = 0.60725, 6.55
        mag = math.hypot(x, y)
        raw_y = min(0xFFFF, int(round(mag / K / (CG / K))))
        phi = math.atan2(x, y) % (2 * math.pi)
        raw_x = int(round(phi * DEG * RAD)) & 0xFFFF
        self.sensor.measure = (t, raw_x, raw_y, (z_hi & 0xFF) << 8)

    def set_raw(self, t, x, y, z):
        self.sensor.measure = (t & 0xFFFF, x & 0xFFFF, y & 0xFFFF, z & 0xFFFF)
