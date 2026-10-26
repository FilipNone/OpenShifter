// SPDX-License-Identifier: MIT
import {
  Checkbox, Dialog, DialogBackdrop, DialogPanel, DialogTitle, Field, Label, Radio, RadioGroup,
} from '@headlessui/react'
import { CheckIcon } from '@heroicons/react/16/solid'
import { ArrowPathIcon, CheckCircleIcon } from '@heroicons/react/20/solid'
import { useCallback, useEffect, useRef, useState } from 'react'
import { FlashError, flash, type Stage } from '../lib/flash'
import type { Bus, ShifterDevice } from '../lib/hid'
import { isKnown, kind, loadImage, type FirmwareImage } from '../lib/image'
import { PID_APP, PID_BOOT, compareVersions } from '../lib/protocol'
import type { Firmware } from '../lib/session'
import { firmwareLabel } from './DevicePanel'
import { Button, Notice, Progress, Section, Spinner, cx, focusRing } from './ui'

interface Manifest { version: string; tag: string; file: string; size: number; sha256: string }

type Bundled =
  | { state: 'loading' }
  | { state: 'ready'; image: FirmwareImage; tag: string }
  | { state: 'missing'; reason: string }

function useBundled(): Bundled {
  const [b, setB] = useState<Bundled>({ state: 'loading' })
  useEffect(() => {
    let live = true
    ;(async () => {
      try {
        const r = await fetch('./firmware/manifest.json', { cache: 'no-cache' })
        // A server without the file can answer with the page itself.
        if (!r.ok || !r.headers.get('content-type')?.includes('json')) {
          throw new Error('this copy of the page has no bundled firmware')
        }
        const m = (await r.json()) as Manifest
        // A manifest without a tag comes from a local build, which the page does not offer.
        if (!m.tag) throw new Error('this copy of the page has no release firmware')
        // A redeploy can rebuild the image under the same name.
        const f = await fetch(`./firmware/${m.file}`, { cache: 'no-cache' })
        if (!f.ok) throw new Error(`could not download ${m.file}`)
        const image = await loadImage(new Uint8Array(await f.arrayBuffer()), m.file)
        if (image.sha256 !== m.sha256) throw new Error(`${m.file} does not match its manifest`)
        if (live) setB({ state: 'ready', image, tag: m.tag })
      } catch (e) {
        if (live) setB({ state: 'missing', reason: e instanceof Error ? e.message : String(e) })
      }
    })()
    return () => { live = false }
  }, [])
  return b
}

const STAGES: { id: Stage; title: string; text: string }[] = [
  { id: 'boot', title: 'Switch to update mode', text: 'The LEDs turn yellow and the shifter restarts as a new USB device.' },
  { id: 'permission', title: 'Allow update mode', text: 'The browser asks once more, because update mode is a separate device.' },
  { id: 'erase', title: 'Erase', text: 'Clears the old firmware, the settings and the calibration.' },
  { id: 'write', title: 'Write', text: 'Sends the new firmware in 60-byte pieces.' },
  { id: 'finish', title: 'Finish', text: 'Tells the shifter the update is complete.' },
  { id: 'restart', title: 'Restart', text: 'The shifter starts the new firmware.' },
  { id: 'verify', title: 'Check', text: 'Reads the version back from the shifter.' },
]

type Run =
  | { state: 'idle' }
  | { state: 'running'; stage: Stage; done: number; total: number; ask: 'boot' | 'app' | null; hidden: boolean }
  | { state: 'done'; version: string | null; image: FirmwareImage }
  | { state: 'failed'; message: string; erased: boolean; stage: Stage }

export function FirmwarePanel({ bus, device, firmware, onBeforeFlash, onFlashed, onCalibrate }: {
  bus: Bus
  device: ShifterDevice
  firmware: Firmware | null
  onBeforeFlash(): Promise<void>
  onFlashed(d: ShifterDevice | null): void
  onCalibrate(): void
}) {
  const bundled = useBundled()
  const [source, setSource] = useState<'bundled' | 'file'>('bundled')
  const [file, setFile] = useState<FirmwareImage | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [override, setOverride] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [run, setRun] = useState<Run>({ state: 'idle' })
  const resolvers = useRef<{ boot?: (d: ShifterDevice | null) => void; app?: (d: ShifterDevice | null) => void }>({})
  const runHeading = useRef<HTMLHeadingElement>(null)

  useEffect(() => { if (bundled.state === 'missing') setSource('file') }, [bundled.state])
  const image = source === 'bundled' ? (bundled.state === 'ready' ? bundled.image : null) : file
  const blocked = image ? image.problems.length > 0 : true
  const needsOverride = image !== null && !isKnown(image)
  const ready = image !== null && !blocked && (!needsOverride || override)

  const running = run.state === 'running'
  useEffect(() => {
    if (!running) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    const vis = () => setRun((r) => (r.state === 'running' && document.hidden ? { ...r, hidden: true } : r))
    window.addEventListener('beforeunload', warn)
    document.addEventListener('visibilitychange', vis)
    let lock: { release(): Promise<void> } | null = null
    const nav = navigator as Navigator & { wakeLock?: { request(t: 'screen'): Promise<{ release(): Promise<void> }> } }
    nav.wakeLock?.request('screen').then((l) => { lock = l }, () => {})
    return () => {
      window.removeEventListener('beforeunload', warn)
      document.removeEventListener('visibilitychange', vis)
      void lock?.release().catch(() => {})
    }
  }, [running])

  useEffect(() => { if (run.state === 'done' || run.state === 'failed') runHeading.current?.focus() }, [run.state])

  const pickFile = async (f: File | undefined) => {
    setFileError(null)
    setOverride(false)
    setFile(null)
    if (!f) return
    if (/\.(elf|hex)$/i.test(f.name)) return setFileError(`Choose the raw .bin image, not the .${f.name.split('.').pop()} file.`)
    if (f.size > 1 << 20) return setFileError(`${f.name} is ${f.size} bytes, far larger than any firmware for the shifter.`)
    setFile(await loadImage(new Uint8Array(await f.arrayBuffer()), f.name))
  }

  const askFor = useCallback((which: 'boot' | 'app') => () => new Promise<ShifterDevice | null>((resolve) => {
    resolvers.current[which] = resolve
    setRun((r) => (r.state === 'running' ? { ...r, ask: which } : r))
  }), [])

  const allow = async (which: 'boot' | 'app') => {
    const d = await bus.request([which === 'boot' ? PID_BOOT : PID_APP]).catch(() => null)
    if (!d) return   // cancelled: the button stays so the user can try again
    resolvers.current[which]?.(d)
    resolvers.current[which] = undefined
    setRun((r) => (r.state === 'running' ? { ...r, ask: null } : r))
  }

  const stopWaiting = (which: 'boot' | 'app') => {
    resolvers.current[which]?.(null)
    resolvers.current[which] = undefined
  }

  const start = async () => {
    if (!image) return
    setConfirm(false)
    setRun({ state: 'running', stage: device.productId === PID_BOOT ? 'erase' : 'boot', done: 0, total: 0, ask: null, hidden: false })
    await onBeforeFlash()
    try {
      const r = await flash(bus, image, {
        device,
        onStage: (stage) => setRun((x) => (x.state === 'running' ? { ...x, stage, ask: stage === 'restart' || stage === 'permission' ? x.ask : null } : x)),
        onProgress: (done, total) => setRun((x) => (x.state === 'running' ? { ...x, done, total } : x)),
        requestBootloader: askFor('boot'),
        requestApplication: askFor('app'),
      })
      setRun({ state: 'done', version: r.version, image })
      onFlashed(r.device)
    } catch (e) {
      const erased = e instanceof FlashError && e.erased
      const message = e instanceof Error ? e.message : String(e)
      setRun((r) => ({ state: 'failed', message, erased, stage: r.state === 'running' ? r.stage : 'boot' }))
    }
  }

  if (run.state !== 'idle') {
    return <RunView run={run} headingRef={runHeading} bootMode={device.productId === PID_BOOT} onAllow={allow} onStop={stopWaiting}
      onClose={() => { if (run.state === 'failed') onFlashed(null); setRun({ state: 'idle' }) }} onCalibrate={onCalibrate} />
  }

  const target = image?.openshifterVersion
  const current = firmware?.kind === 'openshifter' ? firmware.version : null
  const action = !image ? 'Install' :
    firmware?.kind === 'bootloader' ? 'Install and finish the update' :
    !target ? (image.stock ? 'Install stock firmware' : 'Install this image') :
    firmware?.kind === 'stock' ? `Install OpenShifter ${target}` :
    current && compareVersions(target, current) === 0 ? `Reinstall OpenShifter ${target}` :
    current && compareVersions(target, current) < 0 ? `Install older OpenShifter ${target}` :
    `Update to OpenShifter ${target}`

  return (
    <div className="space-y-6">
      {firmware?.kind === 'bootloader' && (
        <Notice tone="warning" title="The shifter is in update mode">
          <p>An update did not finish, or it was started elsewhere. Install a firmware below to bring the shifter back.</p>
        </Notice>
      )}
      <Section title="Firmware" description={<>Running now: <span className="font-medium text-zinc-950 dark:text-white">{firmwareLabel(firmware)}</span></>}>
        <RadioGroup value={source} onChange={setSource} aria-label="Firmware to install" className="grid gap-3 sm:grid-cols-2">
          <Choice value="bundled" disabled={bundled.state !== 'ready'} title={bundled.state === 'ready' ? `OpenShifter ${bundled.image.openshifterVersion}` : 'OpenShifter'}
            text={bundled.state === 'ready' ? `The file from release ${bundled.tag} on GitHub, checked against its SHA-256.` :
              bundled.state === 'loading' ? 'Loading…' : `Unavailable: ${bundled.reason}.`} />
          <Choice value="file" title="A file on this computer" text="A .bin image, for example a stock image from VelocityOne Tuner, to go back to stock." />
        </RadioGroup>

        {source === 'file' && (
          <Field className="mt-5">
            <Label className="text-sm/6 font-medium">Firmware image (.bin)</Label>
            <input type="file" accept=".bin,application/octet-stream" onChange={(e) => void pickFile(e.target.files?.[0])}
              className="mt-1.5 block w-full text-sm/6 text-zinc-700 file:mr-3 file:rounded-lg file:border-0 file:bg-zinc-950/5 file:px-3 file:py-2 file:text-sm/6 file:font-semibold file:text-zinc-950 hover:file:bg-zinc-950/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-led-edge dark:text-zinc-300 dark:file:bg-white/10 dark:file:text-white" />
            {fileError && <p className="mt-2 text-sm/6 text-red-700 dark:text-red-400">{fileError}</p>}
          </Field>
        )}

        {image && <ImageFacts image={image} />}

        {image && image.problems.length > 0 && (
          <div className="mt-4"><Notice tone="danger" title="This file cannot be a firmware for the shifter">
            <ul className="list-disc pl-5">{image.problems.map((p) => <li key={p}>{p}</li>)}</ul>
          </Notice></div>
        )}

        {image && !blocked && needsOverride && (
          <Field className="mt-4 flex items-start gap-3">
            <Checkbox checked={override} onChange={setOverride}
              className={cx('group mt-0.5 flex size-5 shrink-0 cursor-pointer items-center justify-center rounded-md bg-white ring-1 ring-inset ring-zinc-950/20 ' +
                'data-[checked]:bg-led data-[checked]:ring-led-edge dark:bg-white/5 dark:ring-white/20', focusRing)}>
              <CheckIcon className="hidden size-4 fill-led-ink group-data-[checked]:block" />
            </Checkbox>
            <Label className="text-sm/6">
              This image is neither OpenShifter nor a known stock release. I understand it may not start, and that the
              shifter would then need another update from update mode.
            </Label>
          </Field>
        )}

        <div className="mt-6 space-y-4 border-t border-zinc-950/5 pt-5 dark:border-white/10">
          <Notice tone="warning" title="Updating erases the calibration and the settings">
            Calibrate again afterwards. The update takes about {image ? Math.ceil(2 + (Math.ceil(image.data.length / 60) * 0.017)) + 5 : 15} seconds;
            keep the shifter plugged in and this tab in front until it finishes.
          </Notice>
          <Button variant="primary" disabled={!ready} onClick={() => setConfirm(true)}>{action}</Button>
        </div>
      </Section>

      <Dialog open={confirm} onClose={() => setConfirm(false)} className="relative z-50">
        <DialogBackdrop transition className="fixed inset-0 bg-zinc-950/40 transition-opacity duration-200 data-[closed]:opacity-0 dark:bg-zinc-950/70" />
        <div className="fixed inset-0 flex w-screen items-end justify-center overflow-y-auto p-4 sm:items-center">
          <DialogPanel transition className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl ring-1 ring-zinc-950/10 transition duration-200 data-[closed]:translate-y-2 data-[closed]:opacity-0 dark:bg-zinc-800 dark:ring-white/10">
            <DialogTitle className="text-base/7 font-semibold">{action}?</DialogTitle>
            <ul className="mt-3 list-disc space-y-1.5 pl-5 text-sm/6 text-zinc-700 dark:text-zinc-300">
              <li>The calibration, LED colour and button settings are erased. You calibrate again afterwards.</li>
              {!image?.stock && <li>Firmware that is not from Turtle Beach may void the shifter's warranty. OpenShifter comes
                with no warranty: you install it at your own risk.</li>}
              {device.productId === PID_APP && <li>The browser asks for permission once more, for the shifter in update mode.</li>}
              <li>Keep the shifter plugged in and this tab in front. If the update stops, the shifter stays in update mode and you can start again.</li>
            </ul>
            <div className="mt-6 flex flex-col-reverse gap-3 sm:flex-row sm:justify-end">
              <Button onClick={() => setConfirm(false)}>Keep current firmware</Button>
              <Button variant="primary" onClick={start} data-autofocus>Erase and install</Button>
            </div>
          </DialogPanel>
        </div>
      </Dialog>
    </div>
  )
}

function Choice({ value, title, text, disabled }: { value: string; title: string; text: string; disabled?: boolean }) {
  return (
    <Radio value={value} disabled={disabled}
      className={cx('group flex cursor-pointer gap-3 rounded-xl bg-white p-4 ring-1 ring-inset ring-zinc-950/10 data-[checked]:bg-led/10 ' +
        'data-[checked]:ring-2 data-[checked]:ring-led-edge data-[disabled]:cursor-not-allowed data-[disabled]:opacity-60 ' +
        'dark:bg-white/5 dark:ring-white/10 dark:data-[checked]:bg-led/10', focusRing)}>
      <span aria-hidden className="mt-1 flex size-4 shrink-0 items-center justify-center rounded-full ring-1 ring-inset ring-zinc-950/25 group-data-[checked]:bg-led group-data-[checked]:ring-led-edge dark:ring-white/25">
        <span className="size-1.5 rounded-full bg-led-ink opacity-0 group-data-[checked]:opacity-100" />
      </span>
      <span className="min-w-0">
        <span className="block text-sm/6 font-medium">{title}</span>
        <span className="block text-sm/6 text-zinc-600 dark:text-zinc-400">{text}</span>
      </span>
    </Radio>
  )
}

function ImageFacts({ image }: { image: FirmwareImage }) {
  return (
    <dl className="mt-5 grid gap-x-6 gap-y-2 text-sm/6 sm:grid-cols-[8rem_1fr]">
      <dt className="text-zinc-600 dark:text-zinc-400">Image</dt>
      <dd className="font-medium [overflow-wrap:anywhere]">{kind(image)}</dd>
      <dt className="text-zinc-600 dark:text-zinc-400">File</dt>
      <dd className="[overflow-wrap:anywhere]">{image.name}, {image.data.length.toLocaleString('en')} bytes</dd>
      <dt className="text-zinc-600 dark:text-zinc-400">SHA-256</dt>
      <dd><code className="text-xs/5 break-all">{image.sha256}</code></dd>
    </dl>
  )
}

function RunView({ run, headingRef, bootMode, onAllow, onStop, onClose, onCalibrate }: {
  run: Exclude<Run, { state: 'idle' }>
  headingRef: React.RefObject<HTMLHeadingElement>
  bootMode: boolean
  onAllow(which: 'boot' | 'app'): void
  onStop(which: 'boot' | 'app'): void
  onClose(): void
  onCalibrate(): void
}) {
  const stages = STAGES.filter((s) => !bootMode || (s.id !== 'boot' && s.id !== 'permission'))
  // 'done' is not in the list, so it counts as past the last stage.
  const idx = run.state === 'done' ? -1 : stages.findIndex((s) => s.id === run.stage)
  const at = idx < 0 ? stages.length : idx
  const failedAt = run.state === 'failed' ? at : -1

  return (
    <Section title={run.state === 'done' ? 'Update finished' : run.state === 'failed' ? 'Update stopped' : 'Updating'}
      description={run.state === 'running' ? 'Keep the shifter plugged in and this tab in front.' : undefined}>
      {run.state === 'done' && (
        <div className="space-y-4">
          <h3 ref={headingRef} tabIndex={-1} className="sr-only focus:outline-none">Update finished</h3>
          <Notice tone="success" title={run.version ? `OpenShifter ${run.version} is running` : `${kind(run.image)} is installed`}>
            <p>The calibration and settings were reset. Calibrate the shifter now.</p>
          </Notice>
          <div className="flex flex-wrap gap-3">
            <Button variant="primary" onClick={onCalibrate}>Calibrate now</Button>
            <Button onClick={onClose}>Back to Firmware</Button>
          </div>
        </div>
      )}
      {run.state === 'failed' && (
        <div className="mb-6 space-y-4">
          <h3 ref={headingRef} tabIndex={-1} className="sr-only focus:outline-none">Update stopped</h3>
          <Notice tone="danger" role="alert" title={run.erased ? 'The update did not finish' : 'Nothing was installed'}>
            <p>{run.message.charAt(0).toUpperCase() + run.message.slice(1)}</p>
            {run.erased && <p>Connect the shifter again. If it shows up in update mode, the Firmware tab can finish the update.</p>}
          </Notice>
          <Button onClick={onClose}><ArrowPathIcon className="size-4" />Try again</Button>
        </div>
      )}
      {run.state !== 'done' && (
        <ol className="space-y-1">
          {stages.map((s, i) => {
            const state = i === failedAt ? 'failed' : i < at ? 'done' : i === at ? 'now' : 'later'
            return (
              <li key={s.id} aria-current={state === 'now' ? 'step' : undefined}
                className={cx('flex gap-3 rounded-xl p-3', state === 'now' && 'bg-zinc-950/[0.035] dark:bg-white/5')}>
                <span aria-hidden className={cx('mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold ring-1 ring-inset',
                  state === 'done' && 'bg-led text-led-ink ring-led-edge',
                  state === 'now' && 'text-led-text ring-led-edge',
                  state === 'later' && 'text-zinc-500 ring-zinc-950/15 dark:ring-white/15',
                  state === 'failed' && 'bg-red-600 text-white ring-red-600')}>
                  {state === 'done' ? <CheckCircleIcon className="size-4" /> : state === 'now' ? <Spinner className="size-3.5" /> : state === 'failed' ? '!' : i + 1}
                </span>
                <div className="min-w-0 flex-1">
                  <p className={cx('text-sm/6 font-medium', state === 'later' && 'text-zinc-600 dark:text-zinc-400')}>
                    {s.title}<span className="sr-only">{{ done: ' (done)', now: ' (in progress)', later: '', failed: ' (stopped here)' }[state]}</span>
                  </p>
                  {(state === 'now' || state === 'failed') && <p className="text-sm/6 text-zinc-600 dark:text-zinc-400">{s.text}</p>}
                  {run.state === 'running' && state === 'now' && s.id === 'write' && run.total > 0 && (
                    <div className="mt-2 space-y-1.5">
                      <Progress value={run.done} max={run.total} label="Firmware written" />
                      <p className="text-xs/5 tabular-nums text-zinc-600 dark:text-zinc-400">{run.done} of {run.total} pieces</p>
                    </div>
                  )}
                  {run.state === 'running' && state === 'now' && s.id === 'permission' && run.ask === 'boot' && (
                    <div className="mt-3 space-y-2">
                      <p className="text-sm/6">In update mode the shifter is a new entry in the browser's list. Choose it and press Connect.</p>
                      <div className="flex flex-wrap gap-3">
                        <Button variant="primary" onClick={() => onAllow('boot')}>Allow update mode</Button>
                        <Button variant="plain" onClick={() => onStop('boot')}>Stop waiting</Button>
                      </div>
                    </div>
                  )}
                  {run.state === 'running' && state === 'now' && s.id === 'restart' && run.ask === 'app' && (
                    <div className="mt-3 space-y-2">
                      <p className="text-sm/6">The shifter has not come back on its own yet. If it shows up in the browser's list, connect it.</p>
                      <div className="flex flex-wrap gap-3">
                        <Button onClick={() => onAllow('app')}>Connect the restarted shifter</Button>
                        <Button variant="plain" onClick={() => onStop('app')}>Stop waiting</Button>
                      </div>
                    </div>
                  )}
                </div>
              </li>
            )
          })}
        </ol>
      )}
      {run.state === 'running' && run.hidden && (
        <p className="mt-4 text-sm/6 text-amber-800 dark:text-amber-300">
          The tab was in the background, where the browser slows timers. The update continues, but may take longer.
        </p>
      )}
    </Section>
  )
}
