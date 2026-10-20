// SPDX-License-Identifier: MIT
// A simulated shifter for the ?demo page and the tests. Its bootloader checks
// the timing Tuner uses, as SimShifter in tools/openshift_flash.py does.
import type { Bus, InputReport, ShifterDevice } from './hid'
import { findOpenShiftVersion } from './image'
import {
  APP_MAX, BTN_NEUTRAL, BTN_SHIFT_DOWN, BTN_SHIFT_UP, CHUNK, CHUNK_GAP_MS, CMD_BALANCE, CMD_BOOT, CMD_CALIBRATE, CMD_COLOR, CMD_DONE, CMD_ERASE,
  CMD_GAMMA, CMD_HIGHLOW, CMD_LED, CMD_LOGO, CMD_NEUTRAL, CMD_WRITE, DEFAULT_COLOR, DEFAULT_GAINS, ERASE_WAIT_MS,
  MODE_H, MODE_SEQ, OPT_GAMMA, OPT_LOGO, OPT_NEUTRAL, OPT_PRESENT, OPT_RANGE_ON, OPT_RANGE_TOGGLE, PID_APP, PID_BOOT,
  REPORT_ID, REPORT_LEN, VERSION_REPORT_ID, compareVersions, type Point, type RGB,
} from './protocol'

export interface Clock {
  now(): number
  sleep(ms: number): Promise<void>
}

export function virtualClock(): Clock {
  let t = 0
  return { now: () => t, sleep: async (ms) => { t += Math.max(0, ms) } }
}

export const realClock: Clock = {
  now: () => performance.now(),
  sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
}

/** Lever positions the demo can hold, in the shifter's own units, and the
 * field strength there (status byte 48 times 8). Recorded on a real unit:
 * x is mirrored (gear 1 has positive x), and 7 and R, reached with the lever
 * pushed down, read close to neutral in x/y with the weakest field. */
export const POSES: Record<string, Point> = {
  neutral: [-14, -294],
  gear1: [1886, 1673], gear2: [1631, -1809], gear3: [6, 2331], gear4: [-43, -2639],
  gear5: [-1811, 1394], gear6: [-1566, -1392], gear7: [-1010, 190], gear8: [-807, -269],
  handbrake: [0, -2600], 'seq-up': [0, -1900], 'seq-down': [0, 1900],
}
export const RADIUS: Record<string, number> = {
  neutral: 824, gear1: 488, gear2: 400, gear3: 672, gear4: 568, gear5: 408, gear6: 328, gear7: 160, gear8: 136,
  handbrake: 560, 'seq-up': 640, 'seq-down': 700,
}

/** On a real unit, the lever pushed down for 7/R goes across to x -3000,
 * then springs back to about x -1600 before it reaches the gear: x, y and
 * field strength. */
export const UNLOCK: [x: number, y: number, radius: number][] = [[-2976, -154, 552], [-1604, -3, 248]]

/** Output report 0x24 (63 bytes), as the OpenShift descriptor declares it. */
function out24(): HIDReportInfo {
  return { reportId: REPORT_ID, items: [{ reportSize: 8, reportCount: REPORT_LEN }] } as unknown as HIDReportInfo
}

export type BootDescriptor = 'numbered' | 'plain' | 'none'

export interface SimOptions {
  firmware?: Uint8Array | null
  bootMode?: boolean
  clock?: Clock
  bootDescriptor?: BootDescriptor
  /** Grant the bootloader without the chooser, as Chrome may when it
   * remembers the device. */
  bootGranted?: boolean
  /** The written image does not start, so the bootloader comes back. */
  imageFails?: boolean
}

export type Violation = [string, ...number[]]

export class SimShifter {
  readonly clock: Clock
  flash = new Uint8Array(APP_MAX).fill(0xff)
  pid: number
  goneUntil = 0
  violations: Violation[] = []
  log: [number, number][] = []
  bootDescriptor: BootDescriptor
  bootGranted: boolean
  appGranted = true
  imageFails: boolean

  // application state
  color: RGB = DEFAULT_COLOR
  live: RGB | null = null
  gains: RGB = DEFAULT_GAINS
  logo = true
  highlowToggle = false
  highlowOn = false
  gamma = false
  neutralButton = false
  switchH = true
  lever: Point = POSES.neutral
  radius = RADIUS.neutral
  gears: Point[] = Array.from({ length: 8 }, () => [0, 0] as Point)
  neutral: Point = [0, 0]
  handbrake: Point = [0, 0]

  private erasedAt: number | null = null
  private lastChunk: number | null = null
  private nextIndex = 0
  private listeners = new Set<(e: InputReport) => void>()
  private disconnectListeners = new Set<(d: ShifterDevice) => void>()
  private devs: Record<number, ShifterDevice>

  constructor(o: SimOptions = {}) {
    this.clock = o.clock ?? virtualClock()
    if (o.firmware) this.flash.set(o.firmware.subarray(0, APP_MAX))
    this.pid = o.bootMode ? PID_BOOT : PID_APP
    this.bootDescriptor = o.bootDescriptor ?? 'numbered'
    this.bootGranted = o.bootGranted ?? false
    this.imageFails = o.imageFails ?? false
    this.devs = { [PID_APP]: this.device(PID_APP), [PID_BOOT]: this.device(PID_BOOT) }
  }

  get version(): string | null {
    return findOpenShiftVersion(this.flash)
  }

  get present(): boolean {
    return this.clock.now() >= this.goneUntil
  }

  application(n: number): Uint8Array {
    return this.flash.slice(0, n)
  }

  bus(): Bus {
    return {
      simulated: true,
      devices: async () => {
        if (!this.present) return []
        const granted = this.pid === PID_BOOT ? this.bootGranted : this.appGranted
        return granted ? [this.devs[this.pid]] : []
      },
      request: async (pids) => {
        if (!this.present || !pids.includes(this.pid)) return null
        if (this.pid === PID_BOOT) this.bootGranted = true
        else this.appGranted = true
        return this.devs[this.pid]
      },
      onDisconnect: (l) => {
        this.disconnectListeners.add(l)
        return () => { this.disconnectListeners.delete(l) }
      },
      onConnect: () => () => {},
      sleep: (ms) => this.clock.sleep(ms),
      now: () => this.clock.now(),
    }
  }

  private device(pid: number): ShifterDevice {
    const sim = this
    let opened = false
    const collections = (): HIDCollectionInfo[] => {
      if (pid === PID_APP) {
        return [{ usagePage: 1, usage: 4, type: 1, children: [], inputReports: [], featureReports: [],
          outputReports: [out24()] }] as unknown as HIDCollectionInfo[]
      }
      const outputs = { numbered: [out24()], plain: [{ reportId: 0, items: [{ reportSize: 8, reportCount: 64 }] }],
        none: [] }[sim.bootDescriptor]
      return [{ usagePage: 0xff00, usage: 1, type: 1, children: [], inputReports: [], featureReports: [],
        outputReports: outputs }] as unknown as HIDCollectionInfo[]
    }
    const alive = () => sim.present && sim.pid === pid
    return {
      productId: pid,
      productName: 'VelocityOne Multi-Shift',
      get opened() { return opened && alive() },
      get collections() { return collections() },
      async open() {
        if (!alive()) throw new Error('device not present')
        opened = true
      },
      async close() { opened = false },
      async sendReport(reportId, data) {
        if (!opened || !alive()) throw new Error('device not present')
        let bytes = data
        if (reportId === 0) {
          if (data[0] !== REPORT_ID || data.length !== REPORT_LEN + 1) sim.violations.push(['framing', reportId])
          bytes = data.subarray(1)
        } else if (reportId !== REPORT_ID || data.length !== REPORT_LEN) {
          sim.violations.push(['framing', reportId, data.length])
        }
        sim.write(bytes)
        if (!alive()) opened = false
      },
      async receiveFeatureReport(reportId) {
        if (!opened || !alive()) throw new Error('device not present')
        const v = sim.version
        if (pid !== PID_APP || reportId !== VERSION_REPORT_ID || !v || compareVersions(v, '0.4.0') < 0) {
          throw new Error('Failed to receive the feature report.')
        }
        const out = new Uint8Array(16)
        out[0] = VERSION_REPORT_ID
        out.set([...v].map((c) => c.charCodeAt(0)).slice(0, 15), 1)
        return new DataView(out.buffer)
      },
      addEventListener(_t, l) { sim.listeners.add(l) },
      removeEventListener(_t, l) { sim.listeners.delete(l) },
    }
  }

  private write(d: Uint8Array) {
    const cmd = d[0]
    const now = this.clock.now()
    this.log.push([now, cmd])
    if (this.pid === PID_APP) return this.appCommand(d)
    if (cmd === CMD_ERASE) {
      this.flash.fill(0xff)
      this.erasedAt = now
      this.nextIndex = 0
      this.lastChunk = null
    } else if (cmd === CMD_WRITE) {
      const i = (d[1] | (d[2] << 8)) << 16 >> 16
      if (this.erasedAt === null) this.violations.push(['write before erase', i])
      else if (now - this.erasedAt < ERASE_WAIT_MS - 1e-6) this.violations.push(['write during erase', i])
      if (this.lastChunk !== null && now - this.lastChunk < CHUNK_GAP_MS - 1e-6) this.violations.push(['chunks too fast', i])
      if (i !== this.nextIndex) this.violations.push(['out of order', i, this.nextIndex])
      this.flash.set(d.subarray(3, 3 + CHUNK).subarray(0, Math.max(0, APP_MAX - i * CHUNK)), i * CHUNK)
      this.lastChunk = now
      this.nextIndex = i + 1
    } else if (cmd === CMD_DONE) {
      this.resetSettings()
      this.reenumerate(this.imageFails ? PID_BOOT : PID_APP)
    }
  }

  private get isOpenShift() {
    return this.version !== null
  }

  private appCommand(d: Uint8Array) {
    const [cmd, a, b, c, e] = d
    switch (cmd) {
      case CMD_BOOT: return this.reenumerate(PID_BOOT)
      case CMD_LED: this.live = [d[5], d[6], d[7]]; return   // 24 20 0 0 0 0 R G B
      case CMD_CALIBRATE: return this.calibrate(a)
    }
    if (!this.isOpenShift) return
    switch (cmd) {
      case CMD_COLOR: this.color = [a, b, c]; this.live = null; break
      case CMD_LOGO: this.logo = a !== 0; break
      case CMD_HIGHLOW: this.highlowToggle = a !== 0; this.highlowOn = false; break
      case CMD_BALANCE: this.gains = [a, b, c]; void e; break
      case CMD_GAMMA: this.gamma = a !== 0; break
      case CMD_NEUTRAL: if (compareVersions(this.version!, '0.4.1') >= 0) this.neutralButton = a !== 0; break
    }
  }

  private calibrate(n: number) {
    if (n === 9) this.neutral = this.lever
    else if (n === 10) this.handbrake = this.lever
    else if (this.mode === MODE_H && n >= 1 && n <= 8) this.gears[n - 1] = this.lever
  }

  /** Every update erases the settings page. */
  private resetSettings() {
    this.color = DEFAULT_COLOR
    this.live = null
    this.gains = DEFAULT_GAINS
    this.logo = true
    this.highlowToggle = false
    this.gamma = false
    this.neutralButton = false
    this.gears = this.gears.map(() => [0, 0] as Point)
    this.neutral = [0, 0]
    this.handbrake = [0, 0]
  }

  private reenumerate(pid: number) {
    const old = this.devs[this.pid]
    this.pid = pid
    this.goneUntil = this.clock.now() + 1000
    for (const l of this.disconnectListeners) l(old)
  }

  get mode() {
    return this.switchH ? MODE_H : MODE_SEQ
  }

  /** The 64-byte 0x24 status report, ID first. */
  status(): Uint8Array {
    const r = new Uint8Array(64)
    const put = (i: number, v: number) => { r[i] = (v >> 8) & 0xff; r[i + 1] = v & 0xff }
    r[0] = REPORT_ID
    put(2, this.lever[0])
    put(4, this.lever[1])
    r.set(this.live ?? this.color, 6)
    put(9, this.neutral[1])
    put(11, this.handbrake[1])
    this.gears.forEach(([x, y], k) => { put(13 + 6 * k, x); put(15 + 6 * k, y) })
    r[18] = this.switchH ? 0x04 : 0
    r[48] = Math.min(255, Math.round(this.radius / 8))
    if (this.isOpenShift) {
      r[23] = OPT_PRESENT | (this.logo ? OPT_LOGO : 0) | (this.highlowToggle ? OPT_RANGE_TOGGLE : 0) |
        (this.highlowOn ? OPT_RANGE_ON : 0) | (this.gamma ? OPT_GAMMA : 0) |
        (this.neutralButton ? OPT_NEUTRAL : 0)
      r[24] = this.gains[0]
      r[53] = this.gains[1]
      r[54] = this.gains[2]
    }
    put(59, this.neutral[0])
    put(61, this.handbrake[0])
    r[63] = this.mode
    return r
  }

  /** Report 1: gears, buttons, handbrake axis. */
  inputs(): Uint8Array {
    const [x, y] = this.lever
    let gears = 0
    let buttons = 0
    if (this.mode === MODE_H) {
      POSES_GEARS.forEach(([gx, gy], k) => { if (Math.abs(x - gx) < 400 && Math.abs(y - gy) < 400) gears |= 1 << k })
      if (this.neutralButton && !gears) buttons |= BTN_NEUTRAL
    } else {
      // The firmware's default thresholds, less its hysteresis. Pulled back
      // shifts up, pushed forward shifts down.
      if (y > 1550) buttons |= BTN_SHIFT_DOWN
      if (y < -1550) buttons |= BTN_SHIFT_UP
    }
    const hb = y < -2000 ? 0xffff : 0
    return new Uint8Array([gears, buttons, hb & 0xff, hb >> 8])
  }

  emit() {
    if (!this.present || this.pid !== PID_APP) return
    const st = this.status()
    const i = this.inputs()
    for (const l of this.listeners) {
      l({ reportId: REPORT_ID, data: new DataView(st.buffer, 1, 63) })
      l({ reportId: 1, data: new DataView(i.buffer) })
    }
  }
}

const POSES_GEARS: Point[] = [1, 2, 3, 4, 5, 6, 7, 8].map((k) => POSES[`gear${k}`])
