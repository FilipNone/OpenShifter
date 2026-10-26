// SPDX-License-Identifier: MIT
// The shifter's HID protocol, as in docs/protocol.md and tools/openshifter_*.py.
// WebHID passes report data without the report ID, so payloads here are the
// bytes after the ID: 63 for report 0x24.

export const VID = 0x10f5
export const PID_APP = 0x7096
export const PID_BOOT = 0x7097

export const REPORT_ID = 0x24
export const VERSION_REPORT_ID = 0x25
export const REPORT_LEN = 63

export const CMD_BOOT = 0x00
export const CMD_ERASE = 0x01
export const CMD_WRITE = 0x02
export const CMD_DONE = 0x03
export const CMD_LED = 0x20
export const CMD_CALIBRATE = 0x21
export const CMD_COLOR = 0x30
export const CMD_LOGO = 0x31
export const CMD_HIGHLOW = 0x32
export const CMD_BALANCE = 0x33
export const CMD_GAMMA = 0x34
export const CMD_NEUTRAL = 0x35

export const CHUNK = 60
export const ERASE_WAIT_MS = 2000
export const CHUNK_GAP_MS = 15

export const APP_BASE = 0x08010000
export const APP_MAX = 0x10000

export const OPT_LOGO = 0x01
export const OPT_RANGE_TOGGLE = 0x02
export const OPT_RANGE_ON = 0x04
export const OPT_GAMMA = 0x08
export const OPT_NEUTRAL = 0x10
export const OPT_PRESENT = 0x80

export type RGB = readonly [number, number, number]
export const DEFAULT_COLOR: RGB = [255, 182, 78]
export const DEFAULT_GAINS: RGB = [255, 162, 96]

export const MODE_H = 0
export const MODE_SEQ = 1
export const MODE_HANDBRAKE = 2

/** Sequential buttons in report 1. The lever is pulled back to shift up and
 * pushed forward to shift down. */
export const BTN_SHIFT_UP = 0x01
export const BTN_SHIFT_DOWN = 0x02
/** HID button 1, held in H-pattern neutral when OPT_NEUTRAL is on (0.5.0 and newer). */
export const BTN_NEUTRAL = 0x10

/** A command for report 0x24: payload, zero padded to 63 bytes. */
export function command(...payload: number[]): Uint8Array {
  if (payload.length > REPORT_LEN) throw new Error('command too long')
  const out = new Uint8Array(REPORT_LEN)
  out.set(payload.map((b) => b & 0xff))
  return out
}

export function chunkCount(n: number): number {
  return Math.ceil(n / CHUNK)
}

/** Chunk i as Tuner sends it: 0xFF fill, then 02, int16 i (LE), 60 bytes. */
export function chunk(image: Uint8Array, i: number): Uint8Array {
  const out = new Uint8Array(REPORT_LEN).fill(0xff)
  out[0] = CMD_WRITE
  out[1] = i & 0xff
  out[2] = (i >> 8) & 0xff
  out.set(image.subarray(i * CHUNK, (i + 1) * CHUNK), 3)
  return out
}

function s16(b: Uint8Array, i: number): number {
  const v = (b[i] << 8) | b[i + 1]
  return v & 0x8000 ? v - 0x10000 : v
}

export type Point = readonly [number, number]

/** The 0x24 status report. `raw` is the full 64 bytes, ID first, so the
 * offsets match docs/protocol.md. */
export class Status {
  readonly raw: Uint8Array

  constructor(raw: Uint8Array) {
    if (raw.length !== 64 || raw[0] !== REPORT_ID) throw new Error('unexpected status report')
    this.raw = raw
  }

  static fromData(data: DataView): Status {
    const raw = new Uint8Array(64)
    raw[0] = REPORT_ID
    raw.set(new Uint8Array(data.buffer, data.byteOffset, Math.min(data.byteLength, 63)), 1)
    return new Status(raw)
  }

  get x() { return s16(this.raw, 2) }
  get y() { return s16(this.raw, 4) }
  get color(): RGB { return [this.raw[6], this.raw[7], this.raw[8]] }
  get switchH() { return (this.raw[18] & 0x04) !== 0 }
  get mode() { return this.raw[63] }
  get hasOptions() { return (this.raw[23] & OPT_PRESENT) !== 0 }
  get logo() { return (this.raw[23] & OPT_LOGO) !== 0 }
  get highlowToggle() { return (this.raw[23] & OPT_RANGE_TOGGLE) !== 0 }
  get highlowOn() { return (this.raw[23] & OPT_RANGE_ON) !== 0 }
  get gamma() { return (this.raw[23] & OPT_GAMMA) !== 0 }
  get neutralButton() { return (this.raw[23] & OPT_NEUTRAL) !== 0 }
  get gains(): RGB { return [this.raw[24], this.raw[53], this.raw[54]] }
  /** Strength of the magnet's field at the sensor, sent as r / 8. */
  get radius() { return this.raw[48] * 8 }

  gear(k: number): Point {
    const i = 13 + 6 * (k - 1)
    return [s16(this.raw, i), s16(this.raw, i + 2)]
  }

  get neutral(): Point { return [s16(this.raw, 59), s16(this.raw, 9)] }
  get handbrake(): Point { return [s16(this.raw, 61), s16(this.raw, 11)] }
}

/** Lever reading from the status report: x, y and the field strength. */
export interface Lever { x: number; y: number; radius: number }

/** Report 1: gears, buttons and the handbrake axis. */
export interface Inputs {
  gears: number      // bit n: gear n+1 (H-pattern)
  buttons: number    // bit 0 shift up, 1 shift down, 2 high/low, 3 handbrake button
  handbrake: number  // 0..65535
}

export function parseInputs(data: DataView): Inputs {
  return {
    gears: data.getUint8(0),
    buttons: data.getUint8(1),
    handbrake: data.byteLength >= 4 ? data.getUint16(2, true) : 0,
  }
}

export function engagedGear(gears: number): number | null {
  for (let n = 0; n < 8; n++) if (gears & (1 << n)) return n + 1
  return null
}

/** Feature report 0x25 (OpenShifter 0.4.0 and newer): the version string. */
export function parseVersion(data: DataView): string | null {
  let bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength)
  // Chrome includes the report ID on some platforms and not on others.
  if (bytes.length === 16 && bytes[0] === VERSION_REPORT_ID) bytes = bytes.subarray(1)
  let s = ''
  for (const b of bytes) {
    if (b === 0) break
    if (b < 0x20 || b > 0x7e) return null
    s += String.fromCharCode(b)
  }
  return /^\d+\.\d+\.\d+/.test(s) ? s : null
}

export function compareVersions(a: string, b: string): number {
  const pa = a.split(/[^0-9]/).slice(0, 3).map(Number)
  const pb = b.split(/[^0-9]/).slice(0, 3).map(Number)
  for (let i = 0; i < 3; i++) if ((pa[i] || 0) !== (pb[i] || 0)) return (pa[i] || 0) - (pb[i] || 0)
  return 0
}
