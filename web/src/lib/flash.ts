// SPDX-License-Identifier: MIT
// The firmware update, as flash() in tools/openshifter_flash.py, plus the two
// steps WebHID adds: the bootloader is a new USB device, so the browser asks
// for permission once more, and the way to write to it comes from its report
// descriptor, which was never captured.
import type { Bus, ShifterDevice } from './hid'
import { outputReports, reportBytes } from './hid'
import type { FirmwareImage } from './image'
import {
  CHUNK_GAP_MS, CMD_BOOT, CMD_DONE, CMD_ERASE, ERASE_WAIT_MS, PID_APP, PID_BOOT,
  REPORT_ID, REPORT_LEN, VERSION_REPORT_ID, chunk, chunkCount, command, compareVersions, parseVersion,
} from './protocol'

export type Stage = 'boot' | 'permission' | 'erase' | 'write' | 'finish' | 'restart' | 'verify' | 'done'

export class FlashError extends Error {
  /** True once the application area may have been erased. */
  readonly erased: boolean
  constructor(message: string, erased = false) {
    super(message)
    this.erased = erased
  }
}

export interface FlashOptions {
  device: ShifterDevice
  onStage(stage: Stage): void
  onProgress(done: number, total: number): void
  /** Called when the bootloader is not among the granted devices. Resolves
   * with the device the user picked in the chooser, or null if cancelled. */
  requestBootloader(): Promise<ShifterDevice | null>
  /** The same for the restarted application, if it does not come back. */
  requestApplication(): Promise<ShifterDevice | null>
}

export interface FlashResult {
  device: ShifterDevice
  /** Version from feature report 0x25, null for stock or OpenShifter < 0.4. */
  version: string | null
}

/** How to send a 63-byte 0x24 payload to this device, or null if it cannot
 * take one. Both forms put the same 64 bytes on the wire. */
export type WriteForm = { reportId: number; length: number }

export function writeForm(dev: ShifterDevice): WriteForm | null {
  const reports = outputReports(dev.collections)
  const numbered = reports.find((r) => r.reportId === REPORT_ID)
  if (numbered) return reportBytes(numbered) === REPORT_LEN ? { reportId: REPORT_ID, length: REPORT_LEN } : null
  const plain = reports.find((r) => !r.reportId)
  if (plain && reportBytes(plain) === REPORT_LEN + 1) return { reportId: 0, length: REPORT_LEN + 1 }
  return null
}

export async function send(dev: ShifterDevice, form: WriteForm, payload: Uint8Array): Promise<void> {
  if (form.reportId) return dev.sendReport(form.reportId, payload)
  const out = new Uint8Array(form.length)
  out[0] = REPORT_ID
  out.set(payload, 1)
  return dev.sendReport(0, out)
}

async function quietClose(dev: ShifterDevice) {
  try { if (dev.opened) await dev.close() } catch { /* already gone */ }
}

/** How long to wait for the shifter to come back as a new device, including
 * the time the user needs for the chooser. */
export const GIVE_UP_MS = 120_000

/** Waits for a granted device with `pid`, giving the user the chooser after
 * `askAfterMs`. Whichever comes first wins. Gives up after GIVE_UP_MS. */
async function waitFor(bus: Bus, pid: number, askAfterMs: number, ask: () => Promise<ShifterDevice | null>,
  what: string): Promise<ShifterDevice> {
  let asked: Promise<ShifterDevice | null> | null = null
  let picked: ShifterDevice | null | undefined
  const start = bus.now()
  for (;;) {
    const found = (await bus.devices()).find((d) => d.productId === pid)
    if (found) return found
    if (picked) {
      if (picked.productId === pid) return picked
      throw new FlashError(`the device picked is not the shifter ${what}`)
    }
    if (picked === null) throw new FlashError(`no permission for the shifter ${what}`)
    if (bus.now() - start >= GIVE_UP_MS) throw new FlashError(`the shifter ${what} did not appear`)
    if (!asked && bus.now() - start >= askAfterMs) {
      asked = ask()
      asked.then((d) => { picked = d }, () => { picked = null })
    }
    await bus.sleep(100)
  }
}

export async function flash(bus: Bus, image: FirmwareImage, opts: FlashOptions): Promise<FlashResult> {
  let dev = opts.device
  if (dev.productId === PID_APP) {
    opts.onStage('boot')
    try {
      if (!dev.opened) await dev.open()
      await dev.sendReport(REPORT_ID, command(CMD_BOOT))
    } catch (e) {
      throw new FlashError(`could not switch the shifter to update mode (${e instanceof Error ? e.message : e}). ` +
        'Nothing was changed.')
    }
    await quietClose(dev)
    await bus.sleep(500)
    opts.onStage('permission')
    try {
      dev = await waitFor(bus, PID_BOOT, 1500, opts.requestBootloader, 'in update mode')
    } catch (e) {
      throw new FlashError(`${e instanceof Error ? e.message : String(e)}. Nothing was erased, but the shifter may ` +
        'be in update mode: press Try again to connect it and install from there.')
    }
  } else if (dev.productId !== PID_BOOT) {
    throw new FlashError('this is not a VelocityOne Multi-Shift')
  }

  const form = writeForm(dev)
  if (!form) {
    throw new FlashError('the shifter in update mode does not declare a 64-byte output report, so the page ' +
      'cannot write to it. Nothing was erased; the shifter stays in update mode. Use tools/openshifter_flash.py ' +
      'instead, or unplug the shifter and install with VelocityOne Tuner.')
  }

  const n = chunkCount(image.data.length)
  let erased = false
  try {
    if (!dev.opened) await dev.open()
    opts.onStage('erase')
    erased = true
    await send(dev, form, command(CMD_ERASE))
    await bus.sleep(ERASE_WAIT_MS)
    opts.onStage('write')
    opts.onProgress(0, n)
    let next = bus.now()
    for (let i = 0; i < n; i++) {
      const wait = next - bus.now()
      if (wait > 0) await bus.sleep(wait)
      await send(dev, form, chunk(image.data, i))
      next = bus.now() + CHUNK_GAP_MS
      opts.onProgress(i + 1, n)
    }
    await bus.sleep(CHUNK_GAP_MS)
    opts.onStage('finish')
    await send(dev, form, command(CMD_DONE))
  } catch (e) {
    throw new FlashError(`the update was interrupted (${e instanceof Error ? e.message : String(e)}). The ` +
      'shifter should still be in update mode: connect it again and start the update again.', erased)
  } finally {
    await quietClose(dev)
  }

  await bus.sleep(500)
  opts.onStage('restart')
  let app: ShifterDevice
  try {
    app = await waitFor(bus, PID_APP, 3000, opts.requestApplication, 'after the restart')
  } catch (e) {
    throw new FlashError(`the firmware was written, but ${e instanceof Error ? e.message : String(e)}. Unplug the ` +
      'shifter and plug it back in.', true)
  }
  opts.onStage('verify')
  const version = await readVersion(app)
  const expected = image.openshifterVersion
  if (expected && compareVersions(expected, '0.4.0') >= 0 && version !== expected) {
    throw new FlashError(`the shifter restarted but reports ${version ? `OpenShifter ${version}` : 'no OpenShifter ' +
      'version'} instead of OpenShifter ${expected}`, true)
  }
  opts.onStage('done')
  return { device: app, version }
}

/** Feature report 0x25, or null if the firmware has none (stock, < 0.4). */
export async function readVersion(dev: ShifterDevice): Promise<string | null> {
  try {
    if (!dev.opened) await dev.open()
    return parseVersion(await dev.receiveFeatureReport(VERSION_REPORT_ID))
  } catch {
    return null
  }
}
