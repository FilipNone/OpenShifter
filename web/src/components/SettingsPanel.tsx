// SPDX-License-Identifier: MIT
import { Description, Field, Fieldset, Input, Label, Legend, Radio, RadioGroup } from '@headlessui/react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { parseHex, toHex } from '../lib/color'
import {
  CMD_BALANCE, CMD_COLOR, CMD_GAMMA, CMD_HIGHLOW, CMD_LED, CMD_LOGO, CMD_NEUTRAL, DEFAULT_COLOR, DEFAULT_GAINS,
  type RGB, type Status,
} from '../lib/protocol'
import type { Firmware, Session } from '../lib/session'
import { Button, Notice, Section, Spinner, Toggle, cx, focusRing } from './ui'

const PRESETS: [string, RGB][] = [
  ['Warm orange (default)', DEFAULT_COLOR], ['White', [255, 255, 255]], ['Red', [255, 32, 24]],
  ['Racing green', [24, 200, 72]], ['Blue', [40, 110, 255]], ['Violet', [170, 70, 255]],
]

const eq = (a: RGB, b: RGB) => a[0] === b[0] && a[1] === b[1] && a[2] === b[2]

/** Sends the latest value at most every `ms`, always ending on the last one. */
function useLatestSender<T>(send: (v: T) => Promise<void>, ms: number) {
  const state = useRef<{ busy: Promise<void> | null; next: T | null }>({ busy: null, next: null })
  const sendRef = useRef(send)
  sendRef.current = send
  const push = useCallback((v: T) => {
    const s = state.current
    s.next = v
    if (s.busy) return
    s.busy = (async () => {
      while (s.next !== null) {
        const value = s.next
        s.next = null
        try { await sendRef.current(value) } catch { /* the save reports errors */ }
        await new Promise((r) => setTimeout(r, ms))
      }
      s.busy = null
    })()
  }, [ms])
  // Drops a queued value and waits for the one being sent, so a direct
  // write after this is not overtaken by a stale live value.
  const settle = useCallback(async () => {
    state.current.next = null
    await state.current.busy
  }, [])
  return { push, settle }
}

type SaveState = { busy: boolean; error: string | null; saved: boolean }

/** Saving, saved or the error. It sits in space kept for it and fades out,
 * so nothing around it moves when it appears. */
function SavedNote({ state, className }: { state: SaveState; className?: string }) {
  const note = state.busy ? 'busy' : state.error ? 'error' : state.saved ? 'saved' : null
  // Keep the last note while it fades out.
  const last = useRef<{ note: string; error: string | null }>({ note: 'saved', error: null })
  if (note) last.current = { note, error: state.error }
  const shown = last.current
  return (
    <p role="status" className={cx('min-h-6 text-sm/6 transition-opacity duration-300', note ? 'opacity-100' : 'opacity-0', className)}>
      {shown.note === 'busy' && <span className="inline-flex items-center gap-2 text-zinc-600 dark:text-zinc-400"><Spinner />Saving…</span>}
      {shown.note === 'error' && <span className="text-red-700 dark:text-red-400">{shown.error}</span>}
      {shown.note === 'saved' && <span className="text-emerald-700 dark:text-emerald-400">Saved on the shifter.</span>}
    </p>
  )
}

/** How long "Saved on the shifter." stays up. */
const SAVED_MS = 2500

function useSave() {
  const [s, set] = useState<SaveState>({ busy: false, error: null, saved: false })
  const timer = useRef<ReturnType<typeof setTimeout>>()
  useEffect(() => () => clearTimeout(timer.current), [])
  const run = useCallback(async (f: () => Promise<unknown>) => {
    clearTimeout(timer.current)
    set({ busy: true, error: null, saved: false })
    try {
      await f()
      set({ busy: false, error: null, saved: true })
      timer.current = setTimeout(() => set((p) => (p.saved ? { ...p, saved: false } : p)), SAVED_MS)
      return true
    } catch (e) {
      set({ busy: false, error: e instanceof Error ? cap(e.message) : String(e), saved: false })
      return false
    }
  }, [])
  return [s, run] as const
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1) + (s.endsWith('.') ? '' : '.')

function ColorSettings({ session, status, locked }: { session: Session; status: Status; locked: RGB | null }) {
  const current = locked ?? status.color
  const [draft, setDraft] = useState<RGB | null>(null)
  const [hex, setHex] = useState(toHex(current))
  const base = useRef<RGB>(current)
  const [save, run] = useSave()
  if (draft === null) base.current = current
  const shown = draft ?? current
  const preview = useLatestSender((c: RGB) => session.write(CMD_LED, 0, 0, 0, 0, ...c), 40)

  const currentHex = toHex(current)
  useEffect(() => { if (draft === null) setHex(currentHex) }, [draft, currentHex])
  // Leaving with an unsaved preview puts the saved colour back.
  const draftRef = useRef(draft)
  draftRef.current = draft
  const settlePreview = preview.settle
  useEffect(() => () => {
    if (draftRef.current) {
      void settlePreview().then(() => session.write(CMD_LED, 0, 0, 0, 0, ...base.current)).catch(() => {})
    }
  }, [session, settlePreview])

  const pick = (c: RGB) => {
    setDraft(c)
    setHex(toHex(c))
    preview.push(c)
  }
  const revert = () => {
    setDraft(null)
    preview.push(base.current)
  }
  const commit = async () => {
    if (!draft) return
    const c = draft
    await preview.settle()
    if (await run(() => session.setAndCheck([CMD_COLOR, ...c], (st) => eq(st.color, c), 'colour'))) {
      base.current = c
      setDraft(null)
    }
  }
  const dirty = draft !== null && !eq(draft, base.current)
  return (
    <Fieldset className="space-y-4" disabled={locked !== null}>
      <Legend className="!mb-1 text-sm/6 font-semibold">LED colour</Legend>
      <p className="max-w-prose text-sm/6 text-zinc-600 dark:text-zinc-400">
        The LEDs show the colour while you choose it. Save it to keep it after a restart. This page uses the
        same colour as its accent.
      </p>
      <div className="flex flex-wrap items-end gap-4">
        <Field className="flex flex-col gap-1.5">
          <Label className="text-sm/6 font-medium">Colour</Label>
          <input type="color" value={toHex(shown)} onChange={(e) => { const c = parseHex(e.target.value); if (c) pick(c) }}
            className="h-10 w-16 cursor-pointer sm:h-9 rounded-lg bg-white p-1 ring-1 ring-inset ring-zinc-950/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-led-edge disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white/5 dark:ring-white/15" />
        </Field>
        <Field className="flex flex-col gap-1.5">
          <Label className="text-sm/6 font-medium">Hex</Label>
          <Input value={hex} spellCheck={false} autoComplete="off" maxLength={7}
            invalid={parseHex(hex) === null}
            onChange={(e) => { setHex(e.target.value); const c = parseHex(e.target.value); if (c) pick(c) }}
            className={cx('block w-28 rounded-lg bg-white px-3 py-2 text-sm/6 uppercase tabular-nums ring-1 ring-inset ring-zinc-950/10 sm:py-1.5 ' +
              'data-[invalid]:ring-red-600 dark:bg-white/5 dark:ring-white/15', focusRing)} />
        </Field>
        <div className="flex flex-col gap-1.5">
          <span className="text-sm/6 font-medium" id="presets">Presets</span>
          <div role="group" aria-labelledby="presets" className="flex flex-wrap gap-2">
            {PRESETS.map(([name, c]) => (
              <button key={name} type="button" title={name} aria-label={name} aria-pressed={eq(shown, c)} onClick={() => pick(c)}
                disabled={locked !== null}
                className={cx('size-10 rounded-full disabled:cursor-not-allowed disabled:opacity-50 ring-1 ring-inset ring-zinc-950/15 dark:ring-white/20 sm:size-9',
                  eq(shown, c) && 'outline-2 outline-offset-2 outline-zinc-950 dark:outline-white',
                  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-led-edge')}
                style={{ backgroundColor: toHex(c) }} />
            ))}
          </div>
        </div>
      </div>
      {parseHex(hex) === null && <p className="text-sm/6 text-red-700 dark:text-red-400">Type six hex digits, like #FFB64E.</p>}
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" disabled={!dirty || save.busy} onClick={commit}>Save colour</Button>
        <Button disabled={!dirty || save.busy} onClick={revert}>Revert</Button>
        <SavedNote state={save} className="basis-full sm:basis-auto" />
      </div>
    </Fieldset>
  )
}

function SwitchRow({ title, description, checked, onChange, state }:
  { title: string; description: string; checked: boolean; onChange(v: boolean): void; state: SaveState }) {
  return (
    <Field className="flex items-start justify-between gap-6 py-4">
      <div className="min-w-0">
        <Label className="text-sm/6 font-medium">{title}</Label>
        <Description className="text-sm/6 text-zinc-600 dark:text-zinc-400">{description}</Description>
        <SavedNote state={state} />
      </div>
      <Toggle checked={checked} onChange={onChange} disabled={state.busy} />
    </Field>
  )
}

function useImmediate<T>(session: Session, actual: T, payload: (v: T) => number[], check: (st: Status, v: T) => boolean, what: string) {
  const [pending, setPending] = useState<T | null>(null)
  const [state, run] = useSave()
  const set = async (v: T) => {
    setPending(v)
    await run(() => session.setAndCheck(payload(v), (st) => check(st, v), what))
    setPending(null)
  }
  return { value: pending ?? actual, set, state }
}

function WhiteBalance({ session, status, onEditing }:
  { session: Session; status: Status; onEditing(c: RGB | null): void }) {
  const [editing, setEditing] = useState<{ start: RGB; color: RGB } | null>(null)
  const [gains, setGains] = useState<RGB>(status.gains)
  const [save, run] = useSave()
  const live = useLatestSender((g: RGB) => session.write(CMD_BALANCE, ...g, 0), 40)
  const editRef = useRef(editing)
  editRef.current = editing

  const settleLive = live.settle
  const finish = useCallback(async (e: { start: RGB; color: RGB }, restoreGains: boolean) => {
    await settleLive()
    if (restoreGains) await session.write(CMD_BALANCE, ...e.start, 0).catch(() => {})
    await session.write(CMD_LED, 0, 0, 0, 0, ...e.color).catch(() => {})
    onEditing(null)
    setEditing(null)
  }, [session, onEditing, settleLive])

  useEffect(() => () => { if (editRef.current) void finish(editRef.current, true) }, [finish])

  const start = async () => {
    const e = { start: status.gains, color: status.color }
    onEditing(e.color)
    setGains(e.start)
    setEditing(e)
    await session.write(CMD_LED, 0, 0, 0, 0, 255, 255, 255).catch(() => {})
  }
  const change = (i: number, v: number) => {
    const g = [...gains] as unknown as [number, number, number]
    g[i] = Math.max(0, Math.min(255, v))
    setGains(g)
    live.push(g)
  }
  const commit = async () => {
    if (!editing) return
    const g = gains
    await live.settle()
    if (await run(() => session.setAndCheck([CMD_BALANCE, ...g, 1], (st) => eq(st.gains, g), 'white balance'))) {
      await finish(editing, false)
    }
  }

  if (!editing) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-4 py-4">
        <div className="min-w-0">
          <p className="text-sm/6 font-medium">White balance</p>
          <p className="text-sm/6 text-zinc-600 dark:text-zinc-400">
            Red {status.gains[0]}, green {status.gains[1]}, blue {status.gains[2]}. Adjust it if white looks tinted.
          </p>
          <SavedNote state={save} />
        </div>
        <Button onClick={start}>Adjust</Button>
      </div>
    )
  }
  const names = ['Red', 'Green', 'Blue']
  return (
    <Fieldset className="space-y-4 py-4">
      <Legend className="text-sm/6 font-medium">White balance</Legend>
      <Notice title="The lit LEDs now show white">
        Lower the channel that stands out until they look neutral white. Changes show at once; nothing is
        kept until you save.
      </Notice>
      <div className="space-y-3">
        {names.map((n, i) => (
          <Field key={n} className="grid grid-cols-[4rem_1fr_4.5rem] items-center gap-3">
            <Label className="text-sm/6 font-medium">{n}</Label>
            <input type="range" min={0} max={255} value={gains[i]} onChange={(e) => change(i, +e.target.value)}
              className="h-6 w-full cursor-pointer accent-led-edge" />
            <Input type="number" min={0} max={255} value={gains[i]} aria-label={`${n} gain`}
              onChange={(e) => change(i, Number.isFinite(+e.target.value) ? +e.target.value : 0)}
              className={cx('w-full rounded-lg bg-white px-2 py-1.5 text-sm/6 tabular-nums ring-1 ring-inset ring-zinc-950/10 dark:bg-white/5 dark:ring-white/15', focusRing)} />
          </Field>
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="primary" disabled={save.busy} onClick={commit}>Save white balance</Button>
        <Button disabled={save.busy} onClick={() => void finish(editing, true)}>Cancel</Button>
        <Button variant="plain" disabled={save.busy} onClick={() => { setGains(DEFAULT_GAINS); live.push(DEFAULT_GAINS) }}>
          Use defaults
        </Button>
        <SavedNote state={save} className="basis-full sm:basis-auto" />
      </div>
    </Fieldset>
  )
}

export function SettingsPanel({ session, status, firmware, onPinAccent, onOpenFirmware }:
  { session: Session; status: Status | null; firmware: Firmware | null; onPinAccent(c: RGB | null): void; onOpenFirmware(): void }) {
  if (!status || !firmware) return <p className="text-sm/6 text-zinc-600 dark:text-zinc-400">Reading the shifter…</p>
  if (firmware.kind !== 'openshifter') {
    return (
      <Section title="Settings" description="LED colour, the logo LED and the high/low button.">
        <Notice title="These settings need OpenShifter">
          <p>The stock firmware has no way to save them. Install OpenShifter from the Firmware tab first.
            Calibration works with either firmware.</p>
        </Notice>
        <Button className="mt-4" variant="primary" onClick={onOpenFirmware}>Go to Firmware</Button>
      </Section>
    )
  }
  return <OpenShifterSettings session={session} status={status} balance={firmware.balance} neutral={firmware.neutral}
    onPinAccent={onPinAccent} />
}

function OpenShifterSettings({ session, status, balance, neutral, onPinAccent }:
  { session: Session; status: Status; balance: boolean; neutral: boolean; onPinAccent(c: RGB | null): void }) {
  const logo = useImmediate(session, status.logo, (v) => [CMD_LOGO, +v], (st, v) => st.logo === v, 'logo LED')
  const highlow = useImmediate(session, status.highlowToggle, (v) => [CMD_HIGHLOW, +v],
    (st, v) => st.highlowToggle === v, 'high/low')
  const gamma = useImmediate(session, status.gamma, (v) => [CMD_GAMMA, +v], (st, v) => st.gamma === v, 'gamma')
  const neutralButton = useImmediate(session, status.neutralButton, (v) => [CMD_NEUTRAL, +v],
    (st, v) => st.neutralButton === v, 'neutral button')
  const [wbColor, setWbColor] = useState<RGB | null>(null)
  const onEditing = useCallback((c: RGB | null) => { setWbColor(c); onPinAccent(c) }, [onPinAccent])
  const options = [
    { value: false, title: 'Button', text: 'High/low is on while you hold it.' },
    { value: true, title: 'Toggle', text: 'Each press switches high/low on or off. The LED shows the state.' },
  ]
  return (
    <div className="space-y-6">
      <Section title="Lighting" description="Every change here is saved on the shifter and kept after a restart.">
        <ColorSettings session={session} status={status} locked={wbColor} />
        <div className="mt-6 divide-y divide-zinc-950/5 border-t border-zinc-950/5 dark:divide-white/10 dark:border-white/10">
          <SwitchRow title="Logo LED" description="Light the logo between the High/low and Handbrake buttons." checked={logo.value}
            onChange={logo.set} state={logo.state} />
          {balance && (
            <SwitchRow title="Gamma correction" description="Makes dim colours closer to how they look on a screen."
              checked={gamma.value} onChange={gamma.set} state={gamma.state} />
          )}
          {balance && <WhiteBalance session={session} status={status} onEditing={onEditing} />}
        </div>
        {!balance && (
          <p className="mt-4 text-sm/6 text-zinc-600 dark:text-zinc-400">
            White balance and gamma need OpenShifter 0.3.0 or newer.
          </p>
        )}
      </Section>
      <Section title="High/low button" description="How the button next to the lever behaves.">
        <RadioGroup value={highlow.value} onChange={highlow.set} disabled={highlow.state.busy} aria-label="High/low button"
          className="grid gap-3 sm:grid-cols-2">
          {options.map((o) => (
            <Radio key={o.title} value={o.value}
              className={cx('group relative flex cursor-pointer gap-3 rounded-xl bg-white p-4 ring-1 ring-inset ring-zinc-950/10 ' +
                'data-[checked]:bg-led/10 data-[checked]:ring-2 data-[checked]:ring-led-edge data-[disabled]:cursor-not-allowed ' +
                'dark:bg-white/5 dark:ring-white/10 dark:data-[checked]:bg-led/10', focusRing)}>
              <span aria-hidden className="mt-1 flex size-4 shrink-0 items-center justify-center rounded-full ring-1 ring-inset ring-zinc-950/25 group-data-[checked]:bg-led group-data-[checked]:ring-led-edge dark:ring-white/25">
                <span className="size-1.5 rounded-full bg-led-ink opacity-0 group-data-[checked]:opacity-100" />
              </span>
              <span className="min-w-0">
                <span className="block text-sm/6 font-medium">{o.title}</span>
                <span className="block text-sm/6 text-zinc-600 dark:text-zinc-400">{o.text}</span>
              </span>
            </Radio>
          ))}
        </RadioGroup>
        <div className="mt-2"><SavedNote state={highlow.state} /></div>
      </Section>
      <Section title="Neutral button" description="For games that keep the last gear until you pick another one.">
        {neutral ? (
          <div className="border-t border-zinc-950/5 dark:border-white/10">
            <SwitchRow title="Press a button in neutral"
              description="With the switch in H, the neutral button is held while no gear is engaged."
              checked={neutralButton.value} onChange={neutralButton.set} state={neutralButton.state} />
          </div>
        ) : (
          <p className="text-sm/6 text-zinc-600 dark:text-zinc-400">
            The neutral button needs OpenShifter 0.5.0 or newer. Update from the Firmware tab.
          </p>
        )}
      </Section>
    </div>
  )
}
