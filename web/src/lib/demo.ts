// SPDX-License-Identifier: MIT
// The ?demo page: a simulated shifter on the real clock. ?demo=stock starts
// with stock firmware, ?demo=update in update mode.
import { POSES, RADIUS, SimShifter, UNLOCK, realClock } from './sim'
import type { Point } from './protocol'

export type DemoStart = 'openshift' | 'stock' | 'update'

export function demoStart(search: string): DemoStart | null {
  const v = new URLSearchParams(search).get('demo')
  if (v === null) return null
  return v === 'stock' || v === 'update' ? v : 'openshift'
}

const AUTO = ['neutral', 'gear1', 'neutral', 'gear2', 'neutral', 'gear3', 'neutral', 'gear4', 'neutral', 'gear5',
  'neutral', 'gear6', 'neutral', 'gear7', 'neutral', 'gear8']

/** Lever x, y and field strength. */
type Stop = [x: number, y: number, radius: number]

export class Demo {
  readonly sim: SimShifter
  pose = 'auto'
  private timers: ReturnType<typeof setInterval>[] = []
  private step = 0
  /** Bumped by each glide, so a new one takes over from the last. */
  private run = 0

  constructor(start: DemoStart) {
    const fw = start === 'openshift' ? new TextEncoder().encode('\0OpenShift 0.5.0\0') : null
    this.sim = new SimShifter({ firmware: fw, clock: realClock, bootMode: start === 'update' })
    const g: Point[] = [1, 2, 3, 4, 5, 6, 7, 8].map((k) => {
      const [x, y] = POSES[`gear${k}`]
      return [x + ((k * 37) % 90) - 45, y - ((k * 53) % 80) + 40]
    })
    this.sim.gears = g
    this.sim.handbrake = [12, -2580]
    this.sim.neutral = [-20, -180]
  }

  /** Starts the report stream and the lever. stop() and start() can repeat,
   * as React's StrictMode does with effects in development. */
  start() {
    if (this.timers.length) return
    this.timers = [setInterval(() => this.sim.emit(), 5), setInterval(() => this.tick(), 900)]
  }

  private tick() {
    if (this.pose !== 'auto') return
    this.step = (this.step + 1) % AUTO.length
    this.go(AUTO[this.step])
  }

  private go(pose: string) {
    const to: Stop = [...POSES[pose], RADIUS[pose]]
    this.glide(pose === 'gear7' || pose === 'gear8' ? [...UNLOCK, to] : [to])
  }

  private glide(path: Stop[]) {
    // Each leg glides over 220 ms.
    const run = ++this.run
    const leg = (i: number) => {
      const from: Stop = [...this.sim.lever, this.sim.radius]
      const p = path[i]
      const t0 = performance.now()
      const move = () => {
        if (run !== this.run) return
        const t = Math.min(1, (performance.now() - t0) / 220)
        const e = t * (2 - t)
        const [x, y, r] = from.map((f, j) => Math.round(f + (p[j] - f) * e))
        this.sim.lever = [x, y]
        this.sim.radius = r
        if (t < 1) setTimeout(move, 16)
        else if (i + 1 < path.length) leg(i + 1)
      }
      move()
    }
    leg(0)
  }

  hold(pose: string) {
    this.pose = pose
    if (pose !== 'auto') this.go(pose)
  }

  setSwitch(h: boolean) {
    this.sim.switchH = h
  }

  stop() {
    for (const t of this.timers) clearInterval(t)
    this.timers = []
  }
}
