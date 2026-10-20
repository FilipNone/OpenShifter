// SPDX-License-Identifier: MIT
import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { accentTokens, contrast, parseHex } from './color'
import { FlashError, flash, writeForm, type Stage } from './flash'
import { orientation, slot, tilt, toGate, toSequential } from './gate'
import type { ShifterDevice } from './hid'
import { findOpenShiftVersion, loadImage, problems } from './image'
import {
  BTN_NEUTRAL, CHUNK, CMD_NEUTRAL, DEFAULT_COLOR, PID_APP, PID_BOOT, REPORT_ID, Status, chunk, chunkCount, command,
  compareVersions, engagedGear, parseVersion,
} from './protocol'
import { POSES, SimShifter, type SimOptions } from './sim'
import { UDEV_RULE } from './udev'

const BIN = join(__dirname, '..', '..', '..', 'firmware', 'build', 'openshift.bin')

/** A minimal image that passes the vector checks. */
function fakeImage(n = 3000, mark = 'OpenShift 9.8.7'): Uint8Array {
  const d = new Uint8Array(n).fill(0x5a)
  const v = new DataView(d.buffer)
  v.setUint32(0, 0x20005000, true)
  v.setUint32(4, 0x08010101, true)
  v.setUint32(8, 0, true)
  v.setUint32(12, 0x08010201, true)
  d.set(new TextEncoder().encode(mark + '\0'), 200)
  return d
}

describe('protocol', () => {
  it('pads commands to 63 bytes after the report ID', () => {
    const c = command(0x33, 1, 2, 3, 1)
    expect(c.length).toBe(63)
    expect([...c.subarray(0, 6)]).toEqual([0x33, 1, 2, 3, 1, 0])
    expect(() => command(...new Array(64).fill(0))).toThrow()
  })

  it('frames chunks as Tuner does', () => {
    const img = new Uint8Array(130).map((_, i) => i)
    expect(chunkCount(130)).toBe(3)
    const c0 = chunk(img, 0)
    expect([...c0.subarray(0, 4)]).toEqual([0x02, 0, 0, 0])
    expect(c0[62]).toBe(59)
    const last = chunk(img, 2)
    expect([...last.subarray(0, 5)]).toEqual([0x02, 2, 0, 120, 121])
    expect(last[3 + 10]).toBe(0xff)
    expect(last[62]).toBe(0xff)
    expect([...chunk(new Uint8Array(70000), 300).subarray(0, 3)]).toEqual([0x02, 300 & 0xff, 300 >> 8])
  })

  it('reads the status report at the documented offsets', () => {
    const sim = new SimShifter({ firmware: fakeImage() })
    sim.lever = [-1234, 567]
    sim.color = [1, 2, 3]
    sim.gears[2] = [-600, 1499]
    sim.neutral = [7, -8]
    sim.handbrake = [9, -2600]
    sim.gains = [200, 150, 100]
    sim.logo = false
    sim.gamma = true
    sim.switchH = false
    const st = new Status(sim.status())
    expect([st.x, st.y]).toEqual([-1234, 567])
    expect(st.color).toEqual([1, 2, 3])
    expect(st.gear(3)).toEqual([-600, 1499])
    expect(st.neutral).toEqual([7, -8])
    expect(st.handbrake).toEqual([9, -2600])
    expect(st.gains).toEqual([200, 150, 100])
    expect([st.hasOptions, st.logo, st.gamma, st.switchH, st.mode]).toEqual([true, false, true, false, 1])
    expect(new Status(new SimShifter().status()).hasOptions).toBe(false)
    expect(() => new Status(new Uint8Array(63))).toThrow()
  })

  it('parses feature report 0x25 with or without the report ID', () => {
    const v = new TextEncoder().encode('0.4.0')
    const withId = new Uint8Array(16); withId[0] = 0x25; withId.set(v, 1)
    const without = new Uint8Array(15); without.set(v)
    expect(parseVersion(new DataView(withId.buffer))).toBe('0.4.0')
    expect(parseVersion(new DataView(without.buffer))).toBe('0.4.0')
    expect(parseVersion(new DataView(new Uint8Array(15).buffer))).toBeNull()
    expect(parseVersion(new DataView(new Uint8Array([0x30, 0x2e, 0x01, 0]).buffer))).toBeNull()
  })

  it('compares versions and finds the engaged gear', () => {
    expect(compareVersions('0.4.0', '0.3.2')).toBeGreaterThan(0)
    expect(compareVersions('0.4.0-rc1', '0.4.0')).toBe(0)
    expect(compareVersions('0.10.0', '0.9.9')).toBeGreaterThan(0)
    expect(engagedGear(0)).toBeNull()
    expect(engagedGear(0b100)).toBe(3)
  })

  it('turns the neutral button on from 0.5.0', async () => {
    const neutralOn = async (version: string) => {
      const sim = new SimShifter({ firmware: fakeImage(4000, `OpenShift ${version}`) })
      const dev = (await sim.bus().devices())[0]
      await dev.open()
      await dev.sendReport(REPORT_ID, command(CMD_NEUTRAL, 1))
      return { sim, on: new Status(sim.status()).neutralButton }
    }
    expect((await neutralOn('0.4.0')).on).toBe(false)
    const { sim, on } = await neutralOn('0.5.0')
    expect(on).toBe(true)
    expect(sim.inputs()[1]).toBe(BTN_NEUTRAL)
    sim.lever = POSES.gear3
    expect([...sim.inputs().subarray(0, 2)]).toEqual([0b100, 0])
    sim.switchH = false
    sim.lever = POSES.neutral
    expect(sim.inputs()[1]).toBe(0)
  })
})

describe('image checks', () => {
  it('accepts a well-formed image and reads its version', async () => {
    const img = await loadImage(fakeImage(), 'x.bin')
    expect(img.problems).toEqual([])
    expect(img.openshiftVersion).toBe('9.8.7')
    expect(img.sha256).toMatch(/^[0-9a-f]{64}$/)
  })

  it('rejects images that cannot run at 0x08010000', () => {
    expect(problems(new Uint8Array(10))[0]).toMatch(/too small/)
    const d = fakeImage()
    new DataView(d.buffer).setUint32(0, 0x08000000, true)
    new DataView(d.buffer).setUint32(4, 0x08000101, true)
    const p = problems(d)
    expect(p.some((s) => s.includes('stack pointer'))).toBe(true)
    expect(p.some((s) => s.startsWith('reset vector'))).toBe(true)
    const even = fakeImage()
    new DataView(even.buffer).setUint32(4, 0x08010100, true)
    expect(problems(even)[0]).toMatch(/reset vector/)
    expect(problems(fakeImage(0x10001)).some((s) => s.includes('does not fit'))).toBe(true)
  })

  it.skipIf(!existsSync(BIN))('accepts the firmware build', async () => {
    const img = await loadImage(new Uint8Array(readFileSync(BIN)), 'openshift.bin')
    expect(img.problems).toEqual([])
    expect(img.openshiftVersion).toMatch(/^\d+\.\d+\.\d+/)
    expect(findOpenShiftVersion(new Uint8Array(4))).toBeNull()
  })
})

describe('gate drawing', () => {
  // Recorded on a unit: its calibration (x is mirrored), then x, y and the
  // field strength (status byte 48 times 8) at points the lever passed.
  const unit: [number, number][] = [[1986, 2400], [2400, -1886], [40, 3175], [70, -2536],
    [-1956, 1720], [-2576, -1856], [-866, 170], [-1116, -236]]
  const o = orientation(unit, [-320, -110])
  const at = (x: number, y: number, r: number) => toGate(tilt(o, [x, y], r))

  it('orients the gate from the calibrated gears', () => {
    expect(o).toEqual({ sx: -1, sy: 1 })
    expect(orientation(unit.map(([x, y]) => [-x, -y] as [number, number]), [320, 110])).toEqual({ sx: 1, sy: -1 })
    expect(orientation(unit.map(() => [0, 0] as [number, number]), [0, 0])).toEqual({ sx: -1, sy: 1 })
  })

  it('places each gear in its slot', () => {
    const gears: [number, number, number][] = [[1886, 1673, 488], [1631, -1809, 400], [6, 2331, 672], [-43, -2639, 568],
      [-1811, 1394, 408], [-1566, -1392, 328], [-1010, 190, 160], [-807, -269, 136]]
    gears.forEach(([x, y, r], k) => {
      const [u, v] = at(x, y, r)
      const [su, sv] = slot(k + 1)
      expect(Math.abs(u - su)).toBeLessThan(0.15)
      expect(Math.abs(v - sv)).toBeLessThan(0.2)
    })
    for (const [x, y, r] of [[-14, -294, 824], [-24, -536, 928]]) {   // neutral, and pushed down there
      const [u, v] = at(x, y, r)
      expect(u).toBeCloseTo(-0.5, 1)
      expect(v).toBe(0)
    }
  })

  it('moves on across the bar on the way to 7/R, where x turns back', () => {
    const path: [number, number, number][] = [[-659, -289, 832], [-1972, -223, 760], [-2976, -154, 552], [-2082, -39, 312], [-1604, -3, 248]]
    const us = path.map(([x, y, r]) => at(x, y, r))
    for (let i = 1; i < us.length; i++) expect(us[i][0]).toBeGreaterThan(us[i - 1][0] - 0.01)
    for (const [, v] of us) expect(Math.abs(v)).toBeLessThan(0.15)
  })

  it('keeps the play between columns on the bar', () => {
    const [u, v] = at(-1372, -500, 800)   // between 3/4 and 5/6
    expect(u).toBeGreaterThan(-0.4)
    expect(u).toBeLessThan(0.4)
    expect(v).toBe(0)
  })

  it('draws the sequential lever forward and back from neutral', () => {
    const seq = (x: number, y: number, r: number) => toSequential(tilt(o, [x, y], r))
    expect(Math.abs(seq(-14, -294, 824))).toBeLessThan(0.1)
    expect(seq(0, 1900, 700)).toBeGreaterThan(0.9)   // pushed forward: shift down
    expect(seq(0, -1900, 640)).toBeLessThan(-0.9)    // pulled back: shift up
    expect(seq(0, 900, 760)).toBeGreaterThan(seq(0, 300, 800))
  })
})

describe('udev rule', () => {
  it('matches the one the flasher prints', () => {
    const py = readFileSync(join(__dirname, '..', '..', '..', 'tools', 'openshift_flash.py'), 'utf8')
    const m = /UDEV_RULE = """\\\n([\s\S]*?)"""/.exec(py)
    expect(m?.[1]).toBe(UDEV_RULE)
  })
})

describe('accent colour', () => {
  it('keeps text and edges readable for any LED colour', () => {
    for (const hex of ['#ffb64e', '#ffffff', '#000000', '#ffff00', '#0000ff', '#18c848', '#202020']) {
      const t = accentTokens(parseHex(hex)!)
      expect(t.accent).toBe(hex)
      expect(contrast(parseHex(t.onAccent)!, parseHex(hex)!)).toBeGreaterThanOrEqual(4.5)
      expect(contrast(parseHex(t.textLight)!, [255, 255, 255])).toBeGreaterThanOrEqual(4.5)
      expect(contrast(parseHex(t.textDark)!, [24, 24, 27])).toBeGreaterThanOrEqual(4.5)
      expect(contrast(parseHex(t.edgeLight)!, [255, 255, 255])).toBeGreaterThanOrEqual(3)
      expect(contrast(parseHex(t.edgeDark)!, [24, 24, 27])).toBeGreaterThanOrEqual(3)
    }
    expect(accentTokens(DEFAULT_COLOR).onAccent).toBe('#09090b')
  })
})

async function runFlash(o: SimOptions, data = fakeImage(), extra: { cancelBoot?: boolean; ignoreApp?: boolean } = {}) {
  const sim = new SimShifter({ firmware: fakeImage(4000, 'OpenShift 0.3.2'), ...o })
  const bus = sim.bus()
  const device = (await bus.devices())[0] ?? (await bus.request([PID_APP, PID_BOOT]))!
  const stages: Stage[] = []
  const image = await loadImage(data, 'test.bin')
  let progress = 0
  const asked: string[] = []
  const result = await flash(bus, image, {
    device,
    onStage: (s) => stages.push(s),
    onProgress: (d) => { progress = d },
    requestBootloader: async () => { asked.push('boot'); return extra.cancelBoot ? null : bus.request([PID_BOOT]) },
    requestApplication: async () => {
      asked.push('app')
      return extra.ignoreApp ? new Promise<null>(() => {}) : bus.request([PID_APP])
    },
  }).catch((e: unknown) => e)
  return { sim, stages, progress, asked, result, image }
}

describe('flash', () => {
  it('updates from the application with the timing Tuner uses', async () => {
    const r = await runFlash({})
    expect(r.result).not.toBeInstanceOf(Error)
    expect(r.sim.violations).toEqual([])
    expect(r.sim.application(r.image.data.length)).toEqual(r.image.data)
    expect(r.sim.application(chunkCount(3000) * CHUNK).subarray(3000).every((b) => b === 0xff)).toBe(true)
    expect(r.stages).toEqual(['boot', 'permission', 'erase', 'write', 'finish', 'restart', 'verify', 'done'])
    expect(r.asked).toEqual(['boot'])
    expect(r.progress).toBe(chunkCount(3000))
    expect(r.sim.pid).toBe(PID_APP)
    // 24 00, 24 01, the chunks, 24 03
    const cmds = r.sim.log.map(([, c]) => c)
    expect(cmds[0]).toBe(0x00)
    expect(cmds[1]).toBe(0x01)
    expect(cmds.filter((c) => c === 0x02).length).toBe(chunkCount(3000))
    expect(cmds.at(-1)).toBe(0x03)
  })

  it('skips the chooser when the bootloader is already granted', async () => {
    const r = await runFlash({ bootGranted: true })
    expect(r.asked).toEqual([])
    expect(r.sim.violations).toEqual([])
  })

  it('finishes an update that starts in update mode', async () => {
    const r = await runFlash({ bootMode: true, bootGranted: true })
    expect(r.stages[0]).toBe('erase')
    expect(r.sim.violations).toEqual([])
    expect(r.sim.pid).toBe(PID_APP)
  })

  it('writes to an ID-less bootloader with the same bytes', async () => {
    const r = await runFlash({ bootDescriptor: 'plain', bootGranted: true })
    expect(r.result).not.toBeInstanceOf(Error)
    expect(r.sim.violations).toEqual([])
    expect(r.sim.application(3000)).toEqual(r.image.data)
  })

  it('refuses to erase a bootloader it cannot write to', async () => {
    const r = await runFlash({ bootDescriptor: 'none', bootGranted: true })
    expect(r.result).toBeInstanceOf(FlashError)
    expect((r.result as FlashError).erased).toBe(false)
    expect(r.sim.log.map(([, c]) => c)).toEqual([0x00])
    expect(r.sim.version).toBe('0.3.2')
  })

  it('stops without changes when the user cancels the permission', async () => {
    const r = await runFlash({}, fakeImage(), { cancelBoot: true })
    expect(r.result).toBeInstanceOf(FlashError)
    expect(r.sim.pid).toBe(PID_BOOT)
    expect(r.sim.version).toBe('0.3.2')
  })

  it('checks the version an OpenShift 0.4+ image reports after the restart', async () => {
    const ok = await runFlash({ bootGranted: true }, fakeImage(3000, 'OpenShift 0.4.0'))
    expect(ok.result).toMatchObject({ version: '0.4.0' })
  })

  it('reports a shifter that does not restart as erased', async () => {
    const r = await runFlash({ bootGranted: true, imageFails: true })
    expect(r.result).toBeInstanceOf(FlashError)
    expect((r.result as FlashError).erased).toBe(true)
    expect(r.asked).toEqual(['app'])
    expect(r.sim.pid).toBe(PID_BOOT)
  })

  it('stops waiting for the restart after a while', async () => {
    const r = await runFlash({ bootGranted: true, imageFails: true }, fakeImage(), { ignoreApp: true })
    expect(r.result).toBeInstanceOf(FlashError)
    expect((r.result as FlashError).message).toMatch(/did not appear/)
    expect((r.result as FlashError).erased).toBe(true)
  })

  it('reports an interrupted write as erased', async () => {
    const sim = new SimShifter({ firmware: fakeImage(), bootMode: true, bootGranted: true })
    const bus = sim.bus()
    const dev = (await bus.devices())[0]
    let sent = 0
    const flaky: ShifterDevice = Object.create(dev, {
      sendReport: { value: async (id: number, d: Uint8Array) => { if (++sent > 5) throw new Error('unplugged'); return dev.sendReport(id, d) } },
      opened: { get: () => dev.opened },
    })
    const err = await flash(bus, await loadImage(fakeImage(), 'x.bin'), {
      device: flaky, onStage: () => {}, onProgress: () => {},
      requestBootloader: async () => null, requestApplication: async () => null,
    }).catch((e: unknown) => e)
    expect(err).toBeInstanceOf(FlashError)
    expect((err as FlashError).erased).toBe(true)
    expect((err as Error).message).toMatch(/update mode/)
  })

  it('picks the write form from the descriptor', () => {
    const mk = (outputReports: unknown[]) => ({ collections: [{ outputReports, children: [] }] }) as unknown as ShifterDevice
    const items = (n: number) => [{ reportSize: 8, reportCount: n }]
    expect(writeForm(mk([{ reportId: 0x24, items: items(63) }]))).toEqual({ reportId: 0x24, length: 63 })
    expect(writeForm(mk([{ reportId: 0, items: items(64) }]))).toEqual({ reportId: 0, length: 64 })
    expect(writeForm(mk([{ reportId: 0x24, items: items(32) }]))).toBeNull()
    expect(writeForm(mk([]))).toBeNull()
  })
})
