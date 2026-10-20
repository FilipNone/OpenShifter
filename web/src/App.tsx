// SPDX-License-Identifier: MIT
import { Field, Label, Select, Tab, TabGroup, TabList, TabPanel, TabPanels } from '@headlessui/react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CalibrationPanel } from './components/CalibrationPanel'
import { ConnectPanel } from './components/ConnectPanel'
import { DevicePanel, firmwareLabel } from './components/DevicePanel'
import { FirmwarePanel } from './components/FirmwarePanel'
import { SettingsPanel } from './components/SettingsPanel'
import { Button, Notice, Toggle, cx, focusRing } from './components/ui'
import { useSnapshot } from './hooks'
import { applyAccent, storeAccent, storedAccent } from './lib/color'
import { Demo, demoStart } from './lib/demo'
import { webHidAvailable, webHidBus, type Bus, type ShifterDevice } from './lib/hid'
import { PID_APP, PID_BOOT, VID, type RGB } from './lib/protocol'
import { Session } from './lib/session'

const REPO = 'https://github.com/FilipNone/OpenShifter'

function openError(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e)
  if (/open/i.test(msg) && /linux/i.test(navigator.userAgent)) {
    return `The browser could not open the shifter (${msg}). On Linux this usually means the udev rule below is missing.`
  }
  return `The browser could not open the shifter (${msg}). Close other programs that use it, such as VelocityOne Tuner, and try again.`
}

export default function App() {
  const start = useMemo(() => demoStart(location.search), [])
  const demo = useMemo(() => (start ? new Demo(start) : null), [start])
  const bus: Bus | null = useMemo(() => (demo ? demo.sim.bus() : webHidAvailable() ? webHidBus(VID) : null), [demo])
  const [session, setSession] = useState<Session | null>(null)
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [tab, setTab] = useState(0)
  const [pinned, setPinned] = useState<RGB | null>(null)
  const flashing = useRef(false)
  const [isFlashing, setFlashing] = useState(false)
  const keepFirmwareTab = useRef(false)
  const sessionRef = useRef(session)
  sessionRef.current = session

  const slow = useSnapshot(session, 4)
  const live = useSnapshot(session, 30)
  const firmware = session?.firmware ?? null
  const [fwKind, setFwKind] = useState<string | null>(null)

  // Accent: the shifter's LED colour; the last one seen when disconnected.
  const color = pinned ?? slow.status?.color ?? null
  const colorKey = color?.join(',')
  useEffect(() => {
    const c = color ?? storedAccent()
    applyAccent(c)
    if (color && !pinned) storeAccent(color)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [colorKey, pinned])

  // One Session at a time: the mount, a HID connect event and a click can
  // all try to open the shifter.
  const opening = useRef(false)
  const connect = useCallback(async (d: ShifterDevice) => {
    if (!bus || opening.current) return
    opening.current = true
    setConnecting(true)
    setError(null)
    const s = new Session(d, (ms) => bus.sleep(ms))
    try {
      await s.start()
      setSession(s)
      setNotice(null)
    } catch (e) {
      await s.stop()
      setError(openError(e))
    } finally {
      opening.current = false
      setConnecting(false)
    }
  }, [bus])

  // Open a shifter the page already has permission for.
  const reconnect = useCallback(async () => {
    if (!bus || sessionRef.current || flashing.current) return
    const found = (await bus.devices()).find((d) => d.productId === PID_APP || d.productId === PID_BOOT)
    if (found) await connect(found)
  }, [bus, connect])

  useEffect(() => { void reconnect() }, [reconnect])

  useEffect(() => {
    if (!bus) return
    const offD = bus.onDisconnect((d) => {
      const s = sessionRef.current
      if (!s || s.device !== d || flashing.current) return
      void s.stop()
      setSession(null)
      setNotice('The shifter was disconnected. Plug it back in to continue.')
    })
    const offC = bus.onConnect(() => { void reconnect() })
    return () => { offD(); offC() }
  }, [bus, reconnect])

  useEffect(() => {
    demo?.start()
    return () => demo?.stop()
  }, [demo])

  // Pick the tab that fits the firmware once it is known.
  const kindNow = firmware?.kind ?? null
  // After an update the Firmware tab stays, also when the kind is unchanged.
  useEffect(() => {
    if (keepFirmwareTab.current) {
      if (kindNow) { keepFirmwareTab.current = false; setFwKind(kindNow); setTab(2) }
      return
    }
    if (kindNow === fwKind) return
    setFwKind(kindNow)
    if (kindNow === 'stock' || kindNow === 'bootloader') setTab(2)
    else if (kindNow === 'openshift') setTab(0)
  }, [kindNow, fwKind, session])

  const choose = async () => {
    if (!bus) return
    setError(null)
    try {
      const d = await bus.request([PID_APP, PID_BOOT])
      if (d) await connect(d)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const disconnect = async () => {
    await session?.stop()
    setSession(null)
  }

  const beforeFlash = async () => {
    flashing.current = true
    setFlashing(true)
    await sessionRef.current?.stop()
  }

  // After an update the session moves to the restarted shifter without
  // unmounting the Firmware tab, which shows the result. With no device (the
  // user closed a failed run), look for the shifter again: in update mode it
  // opens straight into the Firmware tab.
  const flashed = async (d: ShifterDevice | null) => {
    flashing.current = false
    setFlashing(false)
    if (d && bus) {
      keepFirmwareTab.current = true
      const s = new Session(d, (ms) => bus.sleep(ms))
      try {
        await s.start()
        setSession(s)
      } catch (e) {
        setSession(null)
        setError(openError(e))
      }
      return
    }
    sessionRef.current = null
    setSession(null)
    setNotice('Connect the shifter again. If it is in update mode, the Firmware tab can finish the update.')
    await reconnect()
  }

  const demoHref = '?demo'
  const name = session?.device.productName || 'VelocityOne Multi-Shift'
  const boot = firmware?.kind === 'bootloader'

  return (
    <div className="flex min-h-dvh flex-col">
      {demo && <DemoBar demo={demo} />}
      <header className="border-b border-zinc-950/5 bg-white/80 backdrop-blur dark:border-white/10 dark:bg-zinc-900/80">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3 sm:px-6">
          <a href="./" className={cx('flex items-center gap-2.5 rounded-lg', focusRing, 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-led-edge')}>
            <span aria-hidden className="relative flex size-7 items-center justify-center rounded-lg bg-zinc-950 dark:bg-white/10">
              <span className="size-3 rounded-full bg-led shadow-[0_0_10px_2px_var(--led)]" />
            </span>
            <span className="text-base/7 font-semibold tracking-tight">OpenShift</span>
          </a>
          <div className="flex items-center gap-2 sm:gap-3">
            {session && (
              <span className="hidden items-center gap-2 text-sm/6 text-zinc-600 sm:inline-flex dark:text-zinc-400">
                <span aria-hidden className="size-2 rounded-full bg-emerald-500" />
                Connected · {firmwareLabel(firmware)}
              </span>
            )}
            {session && !isFlashing && <Button onClick={disconnect}>Disconnect</Button>}
            <a href={REPO} className={cx('rounded-lg px-2 py-2 text-sm/6 font-medium text-zinc-700 hover:text-zinc-950 dark:text-zinc-300 dark:hover:text-white',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-led-edge')}>GitHub</a>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-12">
        {notice && !session && <div className="mx-auto mb-6 max-w-2xl"><Notice tone="info" role="status">{notice}</Notice></div>}
        {!session ? (
          <ConnectPanel supported={bus !== null} busy={connecting} error={error} onConnect={choose} demoHref={demoHref} />
        ) : session ? (
          <div className="grid items-start gap-6 lg:grid-cols-[22rem_minmax(0,1fr)] lg:gap-8">
            <aside className="lg:sticky lg:top-6">
              <DevicePanel snap={live} firmware={firmware} name={name} updating={isFlashing} />
              {error && <div className="mt-4"><Notice tone="danger" role="alert">{error}</Notice></div>}
            </aside>
            <TabGroup selectedIndex={boot ? 0 : tab} onChange={setTab} className="min-w-0">
              <TabList aria-label="Shifter tools" className="flex gap-1 rounded-xl bg-zinc-950/5 p-1 dark:bg-white/5">
                {(boot ? ['Firmware'] : ['Settings', 'Calibration', 'Firmware']).map((t) => (
                  <Tab key={t} className={cx('min-h-10 flex-1 rounded-lg px-3 py-1.5 text-sm/6 font-semibold text-zinc-600 sm:min-h-9 sm:flex-none sm:px-4 ' +
                    'data-[hover]:text-zinc-950 data-[selected]:bg-white data-[selected]:text-zinc-950 data-[selected]:shadow-sm data-[selected]:ring-1 data-[selected]:ring-zinc-950/5 ' +
                    'dark:text-zinc-400 dark:data-[hover]:text-white dark:data-[selected]:bg-white/10 dark:data-[selected]:text-white dark:data-[selected]:ring-white/10', focusRing)}>
                    {t}
                  </Tab>
                ))}
              </TabList>
              <TabPanels className="mt-6">
                {!boot && (
                  <TabPanel unmount={false} className="focus:outline-none">
                    <SettingsPanel session={session} status={slow.status} firmware={firmware} onPinAccent={setPinned}
                      onOpenFirmware={() => setTab(2)} />
                  </TabPanel>
                )}
                {!boot && (
                  <TabPanel className="focus:outline-none">
                    <CalibrationPanel session={session} status={live.status} />
                  </TabPanel>
                )}
                <TabPanel unmount={false} className="focus:outline-none">
                  {bus && <FirmwarePanel bus={bus} device={session.device} firmware={firmware} onBeforeFlash={beforeFlash}
                    onFlashed={flashed} onCalibrate={() => setTab(1)} />}
                </TabPanel>
              </TabPanels>
            </TabGroup>
          </div>
        ) : null}
      </main>

      <footer className="border-t border-zinc-950/5 dark:border-white/10">
        <div className="mx-auto flex max-w-6xl flex-wrap justify-between gap-2 px-4 py-6 text-sm/6 text-zinc-600 sm:px-6 dark:text-zinc-400">
          <p>OpenShift is MIT licensed. It is not made or endorsed by Turtle Beach.</p>
          <p>The page talks to the shifter only through your browser; nothing is uploaded.</p>
        </div>
      </footer>
    </div>
  )
}

function DemoBar({ demo }: { demo: Demo }) {
  const [h, setH] = useState(demo.sim.switchH)
  const [pose, setPose] = useState(demo.pose)
  return (
    <div className="bg-zinc-950 text-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5 text-sm/6 sm:px-6">
        <p className="font-medium"><span className="mr-2 rounded bg-led px-1.5 py-0.5 text-xs font-semibold text-led-ink">Demo</span>
          A simulated shifter. Nothing here talks to USB.</p>
        <Field className="flex items-center gap-2">
          <Label>Switch in H</Label>
          <Toggle checked={h} onChange={(v) => { setH(v); demo.setSwitch(v) }} />
        </Field>
        <Field className="flex items-center gap-2">
          <Label>Lever</Label>
          <Select value={pose} onChange={(e) => { setPose(e.target.value); demo.hold(e.target.value) }}
            className={cx('rounded-md bg-white/10 px-2 py-1 text-sm/6 text-white ring-1 ring-inset ring-white/15 *:text-zinc-950', focusRing)}>
            <option value="auto">Moving through the gears</option>
            {['neutral', 'gear1', 'gear2', 'gear3', 'gear4', 'gear5', 'gear6', 'gear7', 'gear8', 'handbrake', 'seq-up', 'seq-down'].map((p) => (
              <option key={p} value={p}>{p === 'gear8' ? 'Reverse' : p.replace('gear', 'Gear ').replace('seq-', 'Shift ').replace(/^./, (c) => c.toUpperCase())}</option>
            ))}
          </Select>
        </Field>
        <a href="./" className="ml-auto font-semibold underline-offset-4 hover:underline">Leave the demo</a>
      </div>
    </div>
  )
}
