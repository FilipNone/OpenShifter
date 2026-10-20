// SPDX-License-Identifier: MIT
import type { Snapshot } from '../hooks'
import { COLUMNS, gearName, orientation, slot, tilt, toGate, toSequential } from '../lib/gate'
import {
  BTN_SHIFT_DOWN, BTN_SHIFT_UP, MODE_H, MODE_HANDBRAKE, MODE_SEQ, engagedGear, type Lever, type Status,
} from '../lib/protocol'
import type { Firmware } from '../lib/session'
import { cx } from './ui'

export const MODE_NAMES: Record<number, string> = { [MODE_H]: 'H-pattern', [MODE_SEQ]: 'Sequential', [MODE_HANDBRAKE]: 'Handbrake' }

export function firmwareLabel(fw: Firmware | null): string {
  if (!fw) return 'Reading…'
  if (fw.kind === 'bootloader') return 'Update mode'
  if (fw.kind === 'stock') return 'Stock firmware'
  return fw.version ? `OpenShift ${fw.version}` : fw.balance ? 'OpenShift 0.3' : 'OpenShift 0.2'
}

// Gate coordinates to SVG: columns 50 apart, columns 70 tall each way.
const GX = 50
const GY = 70

function Gate({ st, lever, gear, buttons }: { st: Status; lever: Lever | null; gear: number | null; buttons: number }) {
  // The dot shows where the lever is; the gear the firmware reports is lit.
  const o = orientation([1, 2, 3, 4, 5, 6, 7, 8].map((k) => st.gear(k)), st.neutral)
  const l = lever ?? st
  const t = tilt(o, [l.x, l.y], l.radius)
  // Handbrake mode is entered from sequential and moves the lever the same way.
  const seq = st.mode === MODE_SEQ || st.mode === MODE_HANDBRAKE
  const [lu, lv] = seq ? [0, toSequential(t)] : toGate(t)
  const up = (buttons & BTN_SHIFT_UP) !== 0
  const down = (buttons & BTN_SHIFT_DOWN) !== 0
  const label = `Lever at x ${st.x}, y ${st.y}` + (gear ? `, in gear ${gearName(gear)}` : '') +
    (seq && up ? ', shifting up' : '') + (seq && down ? ', shifting down' : '')
  return (
    <figure className="relative">
      <svg viewBox="-100 -100 200 200" role="img" aria-label={label}
        className="aspect-square w-full rounded-xl bg-zinc-100 ring-1 ring-inset ring-zinc-950/5 dark:bg-zinc-950/40 dark:ring-white/10">
        <defs>
          <radialGradient id="glow">
            <stop offset="0" stopColor="var(--led)" stopOpacity=".55" />
            <stop offset="1" stopColor="var(--led)" stopOpacity="0" />
          </radialGradient>
        </defs>
        {seq ? (
          <>
            {/* Pushed forward shifts down, pulled back shifts up. */}
            <line x1="0" y1={-GY} x2="0" y2={GY} strokeWidth="10" strokeLinecap="round" className="stroke-zinc-300 dark:stroke-zinc-700" />
            <Slot x={0} y={-GY} on={down} name="−" big />
            <Slot x={0} y={GY} on={up} name="+" big />
            <circle cx="0" cy="0" r="2.5" className="fill-zinc-400 dark:fill-zinc-500" />
          </>
        ) : (
          <>
            <g className="stroke-zinc-300 dark:stroke-zinc-700" strokeWidth="10" strokeLinecap="round" fill="none">
              <line x1={COLUMNS[0] * GX} y1="0" x2={COLUMNS[3] * GX} y2="0" />
              {COLUMNS.map((u) => <line key={u} x1={u * GX} y1={-GY} x2={u * GX} y2={GY} />)}
            </g>
            {/* 7 and R are past a lockout: the lever is pushed down to cross it. */}
            <line x1={GX} y1="-9" x2={GX} y2="9" strokeWidth="1.5" strokeLinecap="round" className="stroke-zinc-400 dark:stroke-zinc-500" />
            {[1, 2, 3, 4, 5, 6, 7, 8].map((k) => {
              const [u, v] = slot(k)
              return <Slot key={k} x={u * GX} y={-v * GY} on={gear === k} name={gearName(k)} />
            })}
            <circle cx={COLUMNS[1] * GX} cy="0" r="2.5" className="fill-zinc-400 dark:fill-zinc-500" />
          </>
        )}
        <g style={{ transform: `translate(${lu * GX}px, ${-lv * GY}px)` }} className="transition-transform duration-75 ease-out">
          <circle r="20" fill="url(#glow)" />
          <circle r="6.5" fill="var(--led)" stroke="var(--led-edge)" strokeWidth="1.5" />
        </g>
      </svg>
    </figure>
  )
}

/** A gear, or a sequential shift, at the end of its travel. */
function Slot({ x, y, on, name, big }: { x: number; y: number; on: boolean; name: string; big?: boolean }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <circle r="11" className={on ? 'fill-led' : 'fill-white dark:fill-zinc-800'}
        stroke={on ? 'var(--led-edge)' : undefined} strokeWidth={on ? 1 : 0} />
      {!on && <circle r="11" fill="none" className="stroke-zinc-300 dark:stroke-white/15" strokeWidth="1" />}
      <text textAnchor="middle" dy={big ? 5.5 : 4} fontSize={big ? 16 : 11} fontWeight="600"
        className={on ? 'fill-led-ink' : 'fill-zinc-700 dark:fill-zinc-300'}>{name}</text>
    </g>
  )
}

function Pill({ on, children }: { on: boolean; children: string }) {
  return (
    <li className={cx('inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs/5 font-medium ring-1 ring-inset',
      on ? 'bg-led text-led-ink ring-led-edge' : 'text-zinc-600 ring-zinc-950/10 dark:text-zinc-400 dark:ring-white/10')}>
      <span aria-hidden className={cx('size-1.5 rounded-full', on ? 'bg-led-ink' : 'bg-zinc-400 dark:bg-zinc-600')} />
      {children}
      <span className="sr-only">{on ? ' pressed' : ' released'}</span>
    </li>
  )
}

export function DevicePanel({ snap, firmware, name, updating }:
  { snap: Snapshot; firmware: Firmware | null; name: string; updating?: boolean }) {
  const st = updating ? null : snap.status
  const inp = snap.inputs
  const gear = st && st.mode === MODE_H ? engagedGear(inp.gears) : null
  const hb = Math.round((inp.handbrake / 65535) * 100)
  return (
    <section aria-labelledby="device-title"
      className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-zinc-950/5 sm:p-6 dark:bg-zinc-800/60 dark:ring-white/10">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h2 id="device-title" className="text-base/7 font-semibold">{name}</h2>
        <span className="text-sm/6 text-zinc-600 dark:text-zinc-400">{updating ? 'Updating' : firmwareLabel(firmware)}</span>
      </div>
      {!st ? (
        <p className="mt-4 text-sm/6 text-zinc-600 dark:text-zinc-400">
          {updating ? 'Inputs are paused while the Firmware tab updates the shifter.'
            : firmware?.kind === 'bootloader'
            ? 'The shifter is in update mode and sends no inputs until an update finishes.'
            : 'Waiting for the first report…'}
        </p>
      ) : (
        <div className="mt-4 grid grid-cols-[minmax(0,9rem)_1fr] gap-x-4 gap-y-5 sm:grid-cols-[minmax(0,15rem)_1fr] lg:grid-cols-1">
          <Gate st={st} lever={snap.lever} gear={gear} buttons={inp.buttons} />
          <div className="contents sm:block sm:space-y-4 lg:contents lg:space-y-0">
            <dl className="grid grid-cols-1 content-start gap-x-4 gap-y-3 text-sm/6 sm:grid-cols-2 lg:grid-cols-2">
              <div>
                <dt className="text-zinc-600 dark:text-zinc-400">Gear</dt>
                <dd className="text-2xl/8 font-semibold tabular-nums">
                  {st.mode === MODE_H ? (gear ? gearName(gear) : 'N') : '·'}
                  <span className="sr-only">{st.mode === MODE_H ? (gear ? '' : ' (neutral)') : ' (not in H-pattern mode)'}</span>
                </dd>
              </div>
              <div>
                <dt className="text-zinc-600 dark:text-zinc-400">Mode</dt>
                <dd className="font-medium">{MODE_NAMES[st.mode] ?? `Mode ${st.mode}`}</dd>
                <dd className="text-zinc-600 dark:text-zinc-400">Switch in {st.switchH ? 'H' : 'SEQ'}</dd>
              </div>
              <div className="sm:col-span-2">
                <dt className="text-zinc-600 dark:text-zinc-400">Lever</dt>
                <dd className="font-medium tabular-nums">x {st.x}, y {st.y}</dd>
              </div>
            </dl>
            <div className="col-span-2 sm:col-span-1">
              <h3 className="text-sm/6 text-zinc-600 dark:text-zinc-400">Buttons</h3>
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                <Pill on={(inp.buttons & BTN_SHIFT_UP) !== 0}>Shift up</Pill>
                <Pill on={(inp.buttons & BTN_SHIFT_DOWN) !== 0}>Shift down</Pill>
                <Pill on={(inp.buttons & 0x04) !== 0}>High/low</Pill>
                <Pill on={(inp.buttons & 0x08) !== 0}>Handbrake</Pill>
              </ul>
            </div>
            <div className="col-span-2 sm:col-span-1">
              <div className="flex justify-between text-sm/6">
                <span id="hb-label" className="text-zinc-600 dark:text-zinc-400">Handbrake axis</span>
                <span className="tabular-nums">{hb}%</span>
              </div>
              <div role="meter" aria-labelledby="hb-label" aria-valuemin={0} aria-valuemax={100} aria-valuenow={hb}
                className="mt-1.5 h-2 overflow-hidden rounded-full bg-zinc-200 dark:bg-white/10">
                <div className="h-full bg-led" style={{ width: `${hb}%` }} />
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
