// SPDX-License-Identifier: MIT
// An open shifter in application mode: the latest status and inputs, and
// commands that are checked against a fresh status, as in openshift_ctl.py.
import type { InputReport, ShifterDevice } from './hid'
import { readVersion } from './flash'
import { PID_BOOT, REPORT_ID, Status, command, compareVersions, parseInputs, type Inputs, type Lever } from './protocol'

export type Firmware =
  | { kind: 'bootloader' }
  | { kind: 'stock' }
  /** `version` is exact from 0.4.0 on; older builds only show what they support. */
  | { kind: 'openshift'; version: string | null; balance: boolean; neutral: boolean }

export class SessionError extends Error {}

/** Status reports averaged for the lever: 24 ms at the shifter's rate. */
const LEVER_SAMPLES = 12

export class Session {
  readonly device: ShifterDevice
  status: Status | null = null
  inputs: Inputs = { gears: 0, buttons: 0, handbrake: 0 }
  version: string | null = null
  private waiters: ((s: Status) => void)[] = []
  private recent: Lever[] = []
  private onReport = (e: InputReport) => {
    if (e.reportId === REPORT_ID && e.data.byteLength >= 63) {
      this.status = Status.fromData(e.data)
      this.recent.push({ x: this.status.x, y: this.status.y, radius: this.status.radius })
      if (this.recent.length > LEVER_SAMPLES) this.recent.shift()
      const w = this.waiters
      this.waiters = []
      for (const f of w) f(this.status)
    } else if (e.reportId === 1 && e.data.byteLength >= 2) {
      this.inputs = parseInputs(e.data)
    }
  }

  constructor(device: ShifterDevice, private sleep: (ms: number) => Promise<void>) {
    this.device = device
  }

  /** The lever averaged over the last few reports. The field strength comes
   * in steps of 8, and the average steadies the tilt worked out from it. */
  get lever(): Lever | null {
    const n = this.recent.length
    if (!n) return null
    let x = 0
    let y = 0
    let radius = 0
    for (const l of this.recent) {
      x += l.x
      y += l.y
      radius += l.radius
    }
    return { x: x / n, y: y / n, radius: radius / n }
  }

  async start() {
    if (!this.device.opened) await this.device.open()
    this.device.addEventListener('inputreport', this.onReport)
    if (this.device.productId === PID_BOOT) return
    this.version = await readVersion(this.device)
    await this.nextStatus(1500)
  }

  async stop() {
    this.device.removeEventListener('inputreport', this.onReport)
    try { if (this.device.opened) await this.device.close() } catch { /* gone */ }
  }

  get firmware(): Firmware | null {
    if (this.device.productId === PID_BOOT) return { kind: 'bootloader' }
    const st = this.status
    if (!st) return null
    if (!st.hasOptions) return { kind: 'stock' }
    const balance = this.version !== null || st.gains.some((g) => g !== 0)
    const neutral = this.version !== null && compareVersions(this.version, '0.4.1') >= 0
    return { kind: 'openshift', version: this.version, balance, neutral }
  }

  nextStatus(timeoutMs = 1000): Promise<Status> {
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => {
        this.waiters = this.waiters.filter((w) => w !== done)
        reject(new SessionError('no status report from the shifter'))
      }, timeoutMs)
      const done = (s: Status) => { clearTimeout(t); resolve(s) }
      this.waiters.push(done)
    })
  }

  /** A status sent after everything already written took effect. */
  async freshStatus(): Promise<Status> {
    await this.sleep(100)
    return this.nextStatus()
  }

  async write(...payload: number[]) {
    try {
      await this.device.sendReport(REPORT_ID, command(...payload))
    } catch (e) {
      throw new SessionError(`could not send to the shifter: ${e instanceof Error ? e.message : e}`)
    }
  }

  async setAndCheck(payload: number[], check: (s: Status) => boolean, what: string): Promise<Status> {
    await this.write(...payload)
    const st = await this.freshStatus()
    if (!check(st)) throw new SessionError(`the shifter did not apply the ${what} change`)
    return st
  }
}
