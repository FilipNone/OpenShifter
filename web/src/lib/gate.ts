// SPDX-License-Identifier: MIT
// Draws the lever in a symmetrical H gate: columns 1/2, 3/4, 5/6 and 7/R.
//
// The sensor is a 3-axis magnetometer. Its x/y follow the direction the lever
// leans, but their size saturates, so in x/y the outer columns are arcs and
// the path into 7/R doubles back. The lever's tilt from upright does neither:
// atan2(sideways field, Z), with Z recovered from the field strength in
// status byte 48. In tilt the gate is a plain H, and as a ratio of two field
// components the tilt does not depend on the magnet's strength.
import type { Point } from './protocol'

/** Gate coordinates: u is the column, -1.5 (1/2) to 1.5 (7/R); v is 1 at the
 * top of a column, 0 on the neutral bar, -1 at the bottom. */
export type GatePoint = [u: number, v: number]

export const COLUMNS = [-1.5, -0.5, 0.5, 1.5] as const

export function slot(gear: number): GatePoint {
  return [COLUMNS[(gear - 1) >> 1], gear % 2 === 1 ? 1 : -1]
}

export function gearName(gear: number): string {
  return gear === 8 ? 'R' : String(gear)
}

/** The gate in degrees of tilt, recorded on a unit: the columns, the top and
 * bottom of each, and neutral. 7 and R travel less because the lever is
 * pushed down for them. */
const COL_DEG = [-40, 0, 46, 70]
const TOP_DEG = [35, 31, 33, 13]
const BOTTOM_DEG = [-46, -43, -45, -21]
const NEUTRAL_DEG = -4

/** Which way round the sensor reads: sx makes gear 1 left, sy makes odd
 * gears up. */
export interface Orientation { sx: 1 | -1; sy: 1 | -1 }

/** As the recorded unit reads: gear 1 has positive x. */
const DEFAULT_ORIENTATION: Orientation = { sx: -1, sy: 1 }

/** The orientation from the calibrated gears 1-6, if they are stored. */
export function orientation(gears: readonly Point[], neutral: Point): Orientation {
  const six = gears.slice(0, 6)
  if (six.length < 6 || six.some(([x, y]) => x === 0 && y === 0)) return DEFAULT_ORIENTATION
  // Columns run 1/2, 3/4, 5/6 from left to right, and odd gears are up.
  let colX = 0
  let upY = 0
  six.forEach(([x, y], k) => {
    colX += ((k >> 1) - 1) * (x - neutral[0])
    upY += (k % 2 === 0 ? 1 : -1) * (y - neutral[1])
  })
  if (colX === 0 || upY === 0) return DEFAULT_ORIENTATION
  return { sx: colX < 0 ? -1 : 1, sy: upY < 0 ? -1 : 1 }
}

/** Lever tilt in degrees, sideways (gear 1 negative) and forward (odd gears
 * positive). `radius` is the field strength, status byte 48 times 8. */
export function tilt(o: Orientation, [x, y]: Point, radius: number): Point {
  // x/y are the sideways field scaled by 6.55; byte 48 truncates r / 8, so
  // the middle of its step is the better guess.
  const side = Math.hypot(x, y) / 6.55
  const r = radius + 4
  const z = Math.sqrt(Math.max(0, r * r - side * side))
  const deg = (Math.atan2(side, z) * 180) / Math.PI
  const dir = Math.atan2(o.sx * x, o.sy * y)
  return [deg * Math.sin(dir), deg * Math.cos(dir)]
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))

/** Piecewise-linear through (xs[i], ys[i]), held flat past the ends. */
function lerp(xs: readonly number[], ys: readonly number[], x: number): number {
  if (x <= xs[0]) return ys[0]
  for (let i = 1; i < xs.length; i++) {
    if (x <= xs[i]) return ys[i - 1] + ((ys[i] - ys[i - 1]) * (x - xs[i - 1])) / (xs[i] - xs[i - 1])
  }
  return ys[ys.length - 1]
}

/** Neutral rests a little off the bar. */
const ON_BAR = 0.1

/** The top and bottom of the column at u. Between two columns the lever is
 * on the bar, so the travel changes over only near their midpoint. */
function travel(u: number): [top: number, bottom: number] {
  const p = clamp(u - COLUMNS[0], 0, 3)
  const i = Math.min(2, Math.floor(p))
  const f = clamp((p - i - 0.35) / 0.3, 0, 1)
  const w = f * f * (3 - 2 * f)
  return [TOP_DEG[i] + (TOP_DEG[i + 1] - TOP_DEG[i]) * w, BOTTOM_DEG[i] + (BOTTOM_DEG[i + 1] - BOTTOM_DEG[i]) * w]
}

/** Forward or back tilt from neutral that draws the lever at the end of the
 * sequential track. The default thresholds fire a little before it. */
const SEQ_DEG = 25

/** Where to draw the lever in sequential mode, from its tilt: 1 pushed fully
 * forward, -1 pulled fully back. */
export function toSequential([, b]: Point): number {
  return clamp((b - NEUTRAL_DEG) / SEQ_DEG, -1, 1)
}

/** Where to draw the lever, from its tilt. The dot moves continuously. On
 * the bar it can sit between columns, as the real lever can, and it is drawn
 * into its column as it goes up or down. */
export function toGate([a, b]: Point): GatePoint {
  const u = lerp(COL_DEG, COLUMNS, a)
  const [top, bottom] = travel(u)
  const raw = clamp(b > NEUTRAL_DEG ? (b - NEUTRAL_DEG) / (top - NEUTRAL_DEG) : (b - NEUTRAL_DEG) / (NEUTRAL_DEG - bottom), -1, 1)
  const v = Math.abs(raw) < ON_BAR ? 0 : (Math.sign(raw) * (Math.abs(raw) - ON_BAR)) / (1 - ON_BAR)
  // Off the bar, the room either side of the column narrows towards the end.
  const col = COLUMNS.reduce((p, c) => (Math.abs(c - u) < Math.abs(p - u) ? c : p))
  const room = Math.abs(v) <= 0.2 ? 0.5 : 0.5 - (0.4 * (Math.abs(v) - 0.2)) / 0.8
  return [col + clamp(u - col, -room, room), v]
}
