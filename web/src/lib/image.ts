// SPDX-License-Identifier: MIT
// Firmware image checks, as in tools/openshift_flash.py (Image).
import { APP_BASE, APP_MAX } from './protocol'

const RAM_BASE = 0x20000000
const RAM_END = 0x20010000

export const KNOWN_STOCK: Record<string, string> = {
  '96da5e6b50f9339b78b99128a069040995fed5e451626a91ffbf7386499dc0d1':
    'Turtle Beach stock firmware TBRS004-20241224 (VelocityOne Tuner 1.1.13.0)',
}

export interface FirmwareImage {
  data: Uint8Array
  name: string
  sha256: string
  openshiftVersion: string | null
  stock: string | null
  problems: string[]
}

export function kind(img: FirmwareImage): string {
  if (img.openshiftVersion) return `OpenShift ${img.openshiftVersion}`
  if (img.stock) return img.stock
  return 'Unrecognised image'
}

export function isKnown(img: FirmwareImage): boolean {
  return Boolean(img.openshiftVersion || img.stock)
}

export function findOpenShiftVersion(data: Uint8Array): string | null {
  const mark = [...'OpenShift '].map((c) => c.charCodeAt(0))
  outer: for (let i = 0; i + mark.length < data.length; i++) {
    for (let j = 0; j < mark.length; j++) if (data[i + j] !== mark[j]) continue outer
    let s = ''
    for (let k = i + mark.length; k < data.length; k++) {
      const c = String.fromCharCode(data[k])
      if (!/[0-9A-Za-z.+-]/.test(c)) break
      s += c
    }
    const m = /^\d+\.\d+\.\d+[0-9A-Za-z.+-]*/.exec(s)
    if (m) return m[0]
  }
  return null
}

function hex(v: number): string {
  return '0x' + (v >>> 0).toString(16).padStart(8, '0')
}

/** Reasons this cannot be an application image for the shifter. */
export function problems(d: Uint8Array): string[] {
  if (d.length < 64) return [`${d.length} bytes is too small for a Cortex-M3 vector table`]
  const out: string[] = []
  if (d.length > APP_MAX) out.push(`${d.length} bytes does not fit the ${APP_MAX} byte application area`)
  const v = new DataView(d.buffer, d.byteOffset, 16)
  const sp = v.getUint32(0, true)
  if (!(RAM_BASE < sp && sp <= RAM_END) || sp & 3) out.push(`initial stack pointer ${hex(sp)} is not in RAM`)
  const end = APP_BASE + d.length
  const vectors: [string, number][] = [['reset', v.getUint32(4, true)], ['NMI', v.getUint32(8, true)],
    ['HardFault', v.getUint32(12, true)]]
  for (const [name, x] of vectors) {
    if (name !== 'reset' && x === 0) continue
    const addr = (x & ~1) >>> 0
    if (!(x & 1) || !(APP_BASE <= addr && addr < end)) {
      out.push(`${name} vector ${hex(x)} is not a Thumb address inside an image linked at ${hex(APP_BASE)}`)
    }
  }
  return out
}

export async function sha256(data: Uint8Array): Promise<string> {
  const copy = new Uint8Array(data)   // a plain ArrayBuffer for subtle.digest
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', copy.buffer))
  return [...h].map((b) => b.toString(16).padStart(2, '0')).join('')
}

export async function loadImage(data: Uint8Array, name: string): Promise<FirmwareImage> {
  const sum = await sha256(data)
  return {
    data,
    name,
    sha256: sum,
    openshiftVersion: findOpenShiftVersion(data),
    stock: KNOWN_STOCK[sum] ?? null,
    problems: problems(data),
  }
}
