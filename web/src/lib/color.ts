// SPDX-License-Identifier: MIT
// The page accent is the shifter's LED colour. The colour itself fills
// accent surfaces; text on it and accent-coloured text are derived so they
// keep WCAG AA contrast whatever the LED colour is.
import { DEFAULT_COLOR, type RGB } from './protocol'

export function toHex([r, g, b]: RGB): string {
  return '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('')
}

export function parseHex(s: string): RGB | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(s.trim())
  if (!m) return null
  const v = parseInt(m[1], 16)
  return [v >> 16, (v >> 8) & 0xff, v & 0xff]
}

export function luminance([r, g, b]: RGB): number {
  const lin = (c: number) => {
    const s = c / 255
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
}

export function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

function mix(a: RGB, b: RGB, t: number): RGB {
  return [0, 1, 2].map((i) => Math.round(a[i] + (b[i] - a[i]) * t)) as unknown as RGB
}

/** The colour nearest to `c`, moving toward `toward`, with `min` contrast
 * against `bg`. */
export function withContrast(c: RGB, bg: RGB, min: number, toward: RGB): RGB {
  if (contrast(c, bg) >= min) return c
  let lo = 0
  let hi = 1
  for (let i = 0; i < 20; i++) {
    const mid = (lo + hi) / 2
    if (contrast(mix(c, toward, mid), bg) >= min) hi = mid
    else lo = mid
  }
  return mix(c, toward, hi)
}

export const INK: RGB = [9, 9, 11]          // zinc-950
export const PAPER: RGB = [255, 255, 255]
export const CANVAS_DARK: RGB = [24, 24, 27] // zinc-900

export interface AccentTokens {
  accent: string       // the LED colour, for fills
  onAccent: string     // text and icons on the fill
  textLight: string    // accent-coloured text on the light canvas
  textDark: string     // the same on the dark canvas
  edgeLight: string    // a 3:1 edge for fills that blend into the canvas
  edgeDark: string
}

export function accentTokens(c: RGB): AccentTokens {
  const onAccent = contrast(INK, c) >= contrast(PAPER, c) ? INK : PAPER
  return {
    accent: toHex(c),
    onAccent: toHex(onAccent),
    textLight: toHex(withContrast(c, PAPER, 4.5, INK)),
    textDark: toHex(withContrast(c, CANVAS_DARK, 4.5, PAPER)),
    edgeLight: toHex(withContrast(c, PAPER, 3, INK)),
    edgeDark: toHex(withContrast(c, CANVAS_DARK, 3, PAPER)),
  }
}

export function applyAccent(c: RGB, root: HTMLElement = document.documentElement) {
  const t = accentTokens(c)
  root.style.setProperty('--led', t.accent)
  root.style.setProperty('--led-ink', t.onAccent)
  root.style.setProperty('--led-text-light', t.textLight)
  root.style.setProperty('--led-text-dark', t.textDark)
  root.style.setProperty('--led-edge-light', t.edgeLight)
  root.style.setProperty('--led-edge-dark', t.edgeDark)
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t.accent)
}

const KEY = 'openshift.accent'

export function storedAccent(): RGB {
  try {
    return parseHex(localStorage.getItem(KEY) ?? '') ?? DEFAULT_COLOR
  } catch {
    return DEFAULT_COLOR
  }
}

export function storeAccent(c: RGB) {
  try { localStorage.setItem(KEY, toHex(c)) } catch { /* private window */ }
}
