// SPDX-License-Identifier: MIT
import { Disclosure, DisclosureButton, DisclosurePanel } from '@headlessui/react'
import { ChevronDownIcon, ClipboardDocumentIcon } from '@heroicons/react/20/solid'
import { useState } from 'react'
import { UDEV_RULE } from '../lib/udev'
import { Button, Notice, Spinner, cx, focusRing } from './ui'

function Copy({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  return (
    <span className="inline-flex items-center gap-2">
      <Button variant="plain" className="!min-h-8 !px-2 !py-1" onClick={() => navigator.clipboard.writeText(text).then(
        () => setState('copied'), () => setState('failed'))}>
        <ClipboardDocumentIcon aria-hidden className="size-4" />{label}
      </Button>
      <span role="status" className="text-xs/5 text-zinc-600 dark:text-zinc-400">
        {state === 'copied' ? 'Copied' : state === 'failed' ? 'Select the text and copy it' : ''}
      </span>
    </span>
  )
}

function Help({ title, children, defaultOpen }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) {
  return (
    <Disclosure as="div" defaultOpen={defaultOpen} className="border-t border-zinc-950/5 dark:border-white/10">
      <DisclosureButton className={cx('group flex w-full items-center justify-between gap-4 py-4 text-left text-sm/6 font-medium', focusRing)}>
        {title}
        <ChevronDownIcon aria-hidden className="size-5 shrink-0 fill-zinc-500 transition-transform group-data-[open]:rotate-180" />
      </DisclosureButton>
      <DisclosurePanel className="space-y-3 pb-5 text-sm/6 text-zinc-700 dark:text-zinc-300">{children}</DisclosurePanel>
    </Disclosure>
  )
}

const commands = `sudo tee /etc/udev/rules.d/70-openshifter.rules >/dev/null <<'EOF'
${UDEV_RULE}EOF
sudo udevadm control --reload-rules`

export function LinuxHelp({ defaultOpen }: { defaultOpen?: boolean }) {
  return (
    <Help title="On Linux, the shifter is missing from the list or does not open" defaultOpen={defaultOpen}>
      <p>The browser needs permission for the shifter's <code>/dev/hidraw</code> device. Install this udev rule once,
        then unplug the shifter and plug it back in:</p>
      <pre className="overflow-x-auto rounded-lg bg-zinc-950 p-3 text-xs/5 text-zinc-100">{commands}</pre>
      <Copy text={commands} label="Copy commands" />
      <p>The rule covers update mode too. It is only for this page and the command-line tools.</p>
    </Help>
  )
}

export function ConnectPanel({ supported, busy, error, onConnect, demoHref }: {
  supported: boolean
  busy: boolean
  error: string | null
  onConnect(): void
  demoHref: string
}) {
  return (
    <div className="mx-auto max-w-2xl">
      <h1 className="text-3xl/10 font-semibold tracking-tight sm:text-4xl/[3rem]">Set up your VelocityOne Multi-Shift</h1>
      <p className="mt-4 max-w-prose text-base/7 text-zinc-700 dark:text-zinc-300">
        OpenShifter is open firmware for the Turtle Beach VelocityOne Multi-Shift. This page installs it over USB,
        changes the LED colour and the high/low button, and calibrates the lever. A shifter with stock firmware
        works too: connect it to install OpenShifter.
      </p>

      <div className="mt-8 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-zinc-950/5 sm:p-6 dark:bg-zinc-800/60 dark:ring-white/10">
        {supported ? (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="primary" onClick={onConnect} disabled={busy}>{busy && <Spinner />}Connect the shifter</Button>
              <a href={demoHref} className={cx('rounded-lg px-2 py-2 text-sm/6 font-semibold text-led-text underline-offset-4 hover:underline', focusRing,
                'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-led-edge')}>Try the demo</a>
            </div>
            <p className="text-sm/6 text-zinc-600 dark:text-zinc-400">
              Plug the shifter in, press Connect, then pick <span className="font-medium text-zinc-950 dark:text-white">VelocityOne Multi-Shift</span> in
              the browser's list. Nothing changes on the shifter until you choose to.
            </p>
            {error && <Notice tone="danger" role="alert" title="Could not connect">{error}</Notice>}
          </div>
        ) : (
          <div className="space-y-4">
            <Notice tone="warning" title="This browser cannot reach USB devices">
              <p>The page uses WebHID, which Chrome, Edge and Opera have on Windows, macOS, Linux and ChromeOS.
                Firefox and Safari do not have it. Open this page in one of those browsers on a computer.</p>
              <p>Firefox can get it from a third-party add-on.{' '}
                <a href="https://github.com/FilipNone/OpenShifter/wiki/Firefox" className={cx('rounded font-semibold underline underline-offset-4', focusRing)}>How to set it up</a></p>
            </Notice>
            <a href={demoHref} className="inline-block text-sm/6 font-semibold text-led-text underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-led-edge">
              Try the demo with a simulated shifter
            </a>
          </div>
        )}
      </div>

      <div className="mt-8">
        <LinuxHelp />
        <Help title="Going back to the stock firmware">
          <p>The page does not include Turtle Beach's firmware. To go back, choose a stock <code>.bin</code> image
            from VelocityOne Tuner in the Firmware tab, or update the shifter with Tuner on Windows.</p>
        </Help>
        <Help title="What an update changes">
          <p>An update replaces the firmware and erases the settings and the calibration, with stock firmware and
            with OpenShifter alike. The bootloader that receives updates is never written, so an interrupted update can
            be started again.</p>
        </Help>
      </div>
    </div>
  )
}
