// SPDX-License-Identifier: MIT
import { Checkbox, Field, Label } from '@headlessui/react'
import { CheckIcon } from '@heroicons/react/16/solid'
import { useEffect, useRef, useState } from 'react'
import { CMD_CALIBRATE, MODE_H, MODE_SEQ, type Status } from '../lib/protocol'
import type { Session } from '../lib/session'
import { Button, Notice, Section, Spinner, cx, focusRing } from './ui'

interface Step {
  id: string
  target: number
  title: string
  text: string
  needs?: 'H' | 'SEQ'
  result(st: Status): string
}

const pt = ([x, y]: readonly [number, number]) => `x ${x}, y ${y}`

const START: Step[] = [
  { id: 'neutral', target: 9, title: 'Neutral', text: 'Leave the lever in neutral and let go of it.', result: (st) => pt(st.neutral) },
  { id: 'handbrake', target: 10, title: 'Handbrake', text: 'Pull the lever fully back, as a handbrake, and hold it.', result: (st) => pt(st.handbrake) },
]
const SEQ: Step[] = [
  { id: 'seq-down', target: 3, title: 'Shift down', text: 'Push the lever forward, as for a shift down, and hold it.', needs: 'SEQ',
    result: (st) => `threshold y ${Math.max(st.y, 900)}` },
  { id: 'seq-up', target: 4, title: 'Shift up', text: 'Pull the lever back, as for a shift up, and hold it.', needs: 'SEQ',
    result: (st) => `threshold y ${Math.min(st.y, -900)}` },
]
const GEARS: Step[] = [1, 2, 3, 4, 5, 6, 7, 8].map((k) => ({
  id: `gear${k}`, target: k, title: k === 8 ? 'Reverse' : `Gear ${k}`,
  text: k === 8 ? 'Push the lever down to unlock reverse, put it in R and hold it there.'
    : k === 7 ? 'Push the lever down to unlock 7, put it in gear 7 and hold it there.'
    : `Put the lever in gear ${k} and hold it there.`,
  needs: 'H' as const,
  result: (st: Status) => pt(st.gear(k)),
}))

function unmet(step: Step, st: Status): string | null {
  if (step.needs === 'H' && st.mode !== MODE_H) return 'Put the H/SEQ switch in H.'
  if (step.needs === 'SEQ' && st.mode !== MODE_SEQ) return 'Put the H/SEQ switch in SEQ, with handbrake mode off.'
  return null
}

export function CalibrationPanel({ session, status }: { session: Session; status: Status | null }) {
  const [sequential, setSequential] = useState(false)
  const [started, setStarted] = useState(false)
  const [index, setIndex] = useState(0)
  const [done, setDone] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const steps = [...START, ...(sequential ? SEQ : []), ...GEARS]
  const finished = started && index >= steps.length

  useEffect(() => { if (started) heading.current?.focus() }, [index, started])

  if (!status) return <p className="text-sm/6 text-zinc-600 dark:text-zinc-400">Reading the shifter…</p>

  if (!started) {
    return (
      <Section title="Calibration" description="Teaches the shifter where neutral, the handbrake and each gear are. It works with stock firmware and OpenShift.">
        <ul className="max-w-prose list-disc space-y-1 pl-5 text-sm/6 text-zinc-700 dark:text-zinc-300">
          <li>Each step records the lever where it is when you press Record.</li>
          <li>Neutral and handbrake come first, the gears last, as in VelocityOne Tuner.</li>
          <li>Afterwards, unplug the shifter and plug it back in.</li>
        </ul>
        <Field className="mt-5 flex items-start gap-3">
          <Checkbox checked={sequential} onChange={setSequential}
            className={cx('group mt-0.5 flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-md bg-white ring-1 ring-inset ring-zinc-950/20 ' +
              'data-[checked]:bg-led data-[checked]:ring-led-edge dark:bg-white/5 dark:ring-white/20', focusRing)}>
            <CheckIcon className="hidden size-4 fill-led-ink group-data-[checked]:block" />
          </Checkbox>
          <div>
            <Label className="text-sm/6 font-medium">Also set the sequential thresholds</Label>
            <p className="text-sm/6 text-zinc-600 dark:text-zinc-400">How far the lever moves before a shift up or down counts. Needs the switch in SEQ.</p>
          </div>
        </Field>
        <Button className="mt-6" variant="primary" onClick={() => { setStarted(true); setIndex(0); setDone({}); setError(null) }}>
          Start calibration
        </Button>
      </Section>
    )
  }

  const step = steps[Math.min(index, steps.length - 1)]
  const blocker = finished ? null : unmet(step, status)

  const record = async () => {
    setBusy(true)
    setError(null)
    try {
      const st = unmet(step, await session.freshStatus())
      if (st) throw new Error(st)
      await session.write(CMD_CALIBRATE, step.target)
      const after = await session.freshStatus()
      setDone((d) => ({ ...d, [step.id]: step.result(after) }))
      setIndex((i) => i + 1)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title="Calibration"
      description={finished ? undefined : `Step ${index + 1} of ${steps.length}`}
      actions={<Button variant="plain" onClick={() => { setStarted(false); setError(null) }}>Start over</Button>}>
      <div className="grid gap-6 md:grid-cols-[13rem_1fr]">
        <ol aria-label="Calibration steps" className="grid grid-cols-2 gap-1 sm:grid-cols-3 md:grid-cols-1">
          {steps.map((s, i) => {
            const current = i === index
            return (
              <li key={s.id}>
                <button type="button" onClick={() => { setIndex(i); setError(null) }} aria-current={current ? 'step' : undefined}
                  className={cx('flex w-full min-h-10 items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-left text-sm/6 md:min-h-9',
                    current ? 'bg-zinc-950/5 font-semibold dark:bg-white/10' : 'text-zinc-700 hover:bg-zinc-950/5 dark:text-zinc-300 dark:hover:bg-white/5',
                    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-led-edge')}>
                  <span aria-hidden className={cx('flex size-5 shrink-0 items-center justify-center rounded-full text-[0.6875rem] font-semibold ring-1 ring-inset',
                    done[s.id] ? 'bg-led text-led-ink ring-led-edge' : 'ring-zinc-950/20 dark:ring-white/20')}>
                    {done[s.id] ? <CheckIcon className="size-3.5" /> : i + 1}
                  </span>
                  <span className="truncate">{s.title}</span>
                  {done[s.id] && <span className="sr-only">(recorded)</span>}
                </button>
              </li>
            )
          })}
        </ol>

        <div className="min-w-0 space-y-4">
          {finished ? (
            <>
              <h3 ref={heading} tabIndex={-1} className="text-base/7 font-semibold focus:outline-none">
                {Object.keys(done).length ? 'Calibration saved' : 'Nothing was recorded'}
              </h3>
              {Object.keys(done).length > 0 ? (
                <Notice tone="success" title="Unplug the shifter and plug it back in">
                  Until it restarts, the old gear learning keeps adjusting the gears.
                </Notice>
              ) : (
                <p className="text-sm/6 text-zinc-600 dark:text-zinc-400">Every step was skipped, so the calibration is unchanged.</p>
              )}
              <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-sm/6">
                {steps.filter((s) => done[s.id]).map((s) => (
                  <div key={s.id} className="contents">
                    <dt className="text-zinc-600 dark:text-zinc-400">{s.title}</dt>
                    <dd className="tabular-nums">{done[s.id]}</dd>
                  </div>
                ))}
              </dl>
            </>
          ) : (
            <>
              <h3 ref={heading} tabIndex={-1} className="text-base/7 font-semibold focus:outline-none">{step.title}</h3>
              <p className="text-base/7">{step.text}</p>
              <p className="text-sm/6 text-zinc-600 tabular-nums dark:text-zinc-400" aria-live="off">
                Lever now: x {status.x}, y {status.y} · switch in {status.switchH ? 'H' : 'SEQ'}
              </p>
              {done[step.id] && <p className="text-sm/6">Recorded: <span className="tabular-nums">{done[step.id]}</span>. Record again to replace it.</p>}
              {blocker && <Notice tone="warning" role="status">{blocker}</Notice>}
              {error && <Notice tone="danger" role="alert" title="Not recorded">{error}</Notice>}
              <div className="flex flex-wrap gap-3">
                <Button variant="primary" disabled={busy || blocker !== null} onClick={record}>
                  {busy && <Spinner />}Record {step.title.toLowerCase()}
                </Button>
                {index > 0 && <Button disabled={busy} onClick={() => setIndex(index - 1)}>Back</Button>}
                <Button variant="plain" disabled={busy} onClick={() => setIndex(index + 1)}>Skip</Button>
              </div>
            </>
          )}
        </div>
      </div>
    </Section>
  )
}
