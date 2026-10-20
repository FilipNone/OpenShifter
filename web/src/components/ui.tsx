// SPDX-License-Identifier: MIT
import { Button as HButton, Switch, type ButtonProps } from '@headlessui/react'
import { CheckCircleIcon, ExclamationTriangleIcon, InformationCircleIcon, XCircleIcon } from '@heroicons/react/20/solid'
import type { ReactNode } from 'react'

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(' ')
}

const focus = 'focus:outline-none data-[focus]:outline-2 data-[focus]:outline-offset-2 data-[focus]:outline-led-edge'

const variants = {
  primary: 'bg-led text-led-ink shadow-sm ring-1 ring-inset ring-zinc-950/10 data-[hover]:brightness-105 ' +
    'data-[active]:brightness-95 dark:ring-white/15',
  secondary: 'bg-white text-zinc-950 shadow-sm ring-1 ring-inset ring-zinc-950/10 data-[hover]:bg-zinc-50 ' +
    'data-[active]:bg-zinc-100 dark:bg-white/5 dark:text-white dark:ring-white/15 dark:data-[hover]:bg-white/10',
  plain: 'text-zinc-950 data-[hover]:bg-zinc-950/5 data-[active]:bg-zinc-950/10 dark:text-white ' +
    'dark:data-[hover]:bg-white/10',
  danger: 'bg-red-600 text-white shadow-sm data-[hover]:bg-red-500 data-[active]:bg-red-700',
}

export function Button({ variant = 'secondary', className, ...p }:
  ButtonProps & { variant?: keyof typeof variants; className?: string }) {
  return (
    <HButton
      {...p}
      className={cx('inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm/6 ' +
        'font-semibold transition-[filter,background-color] data-[disabled]:cursor-not-allowed ' +
        'data-[disabled]:opacity-50 sm:min-h-9 sm:py-1.5', focus, variants[variant], className)}
    />
  )
}

export function Section({ title, description, children, actions, id }:
  { title: string; description?: ReactNode; children: ReactNode; actions?: ReactNode; id?: string }) {
  return (
    <section aria-labelledby={id} className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-zinc-950/5 sm:p-6 dark:bg-zinc-800/60 dark:ring-white/10">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h2 id={id} className="text-base/7 font-semibold">{title}</h2>
          {description && <p className="mt-1 max-w-prose text-sm/6 text-zinc-600 dark:text-zinc-400">{description}</p>}
        </div>
        {actions}
      </div>
      <div className="mt-5">{children}</div>
    </section>
  )
}

const tones = {
  info: { icon: InformationCircleIcon, box: 'bg-sky-50 text-sky-950 ring-sky-600/20 dark:bg-sky-400/10 dark:text-sky-100 dark:ring-sky-400/25', ic: 'text-sky-600 dark:text-sky-400' },
  warning: { icon: ExclamationTriangleIcon, box: 'bg-amber-50 text-amber-950 ring-amber-600/25 dark:bg-amber-400/10 dark:text-amber-100 dark:ring-amber-400/25', ic: 'text-amber-600 dark:text-amber-400' },
  danger: { icon: XCircleIcon, box: 'bg-red-50 text-red-950 ring-red-600/20 dark:bg-red-400/10 dark:text-red-100 dark:ring-red-400/25', ic: 'text-red-600 dark:text-red-400' },
  success: { icon: CheckCircleIcon, box: 'bg-emerald-50 text-emerald-950 ring-emerald-600/20 dark:bg-emerald-400/10 dark:text-emerald-100 dark:ring-emerald-400/25', ic: 'text-emerald-600 dark:text-emerald-400' },
}

export function Notice({ tone = 'info', title, children, role }:
  { tone?: keyof typeof tones; title?: ReactNode; children?: ReactNode; role?: 'status' | 'alert' }) {
  const t = tones[tone]
  const Icon = t.icon
  return (
    <div role={role} className={cx('flex gap-3 rounded-xl p-4 text-sm/6 ring-1 ring-inset', t.box)}>
      <Icon aria-hidden className={cx('mt-0.5 size-5 shrink-0', t.ic)} />
      <div className="min-w-0 space-y-1 [overflow-wrap:anywhere]">
        {title && <p className="font-semibold">{title}</p>}
        {children}
      </div>
    </div>
  )
}

export function Toggle({ checked, onChange, disabled, label }:
  { checked: boolean; onChange(v: boolean): void; disabled?: boolean; label?: string }) {
  return (
    <Switch
      checked={checked}
      onChange={onChange}
      disabled={disabled}
      aria-label={label}
      className={cx('group relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full bg-zinc-200 p-0.5 ' +
        'ring-1 ring-inset ring-zinc-950/10 transition-colors duration-200 data-[checked]:bg-led ' +
        'data-[checked]:ring-led-edge data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50 ' +
        'dark:bg-white/10 dark:ring-white/15', focus)}
    >
      <span aria-hidden className="pointer-events-none inline-block size-5 rounded-full bg-white shadow ring-1 ring-zinc-950/10 transition-transform duration-200 group-data-[checked]:translate-x-5" />
    </Switch>
  )
}

export function Progress({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = max ? Math.round((value / max) * 100) : 0
  return (
    <div>
      <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={value}
        aria-valuetext={`${pct} percent`}
        className="h-2.5 overflow-hidden rounded-full bg-zinc-200 ring-1 ring-inset ring-zinc-950/5 dark:bg-white/10">
        <div className="h-full rounded-full bg-led transition-[width] duration-150" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" className={cx('size-4 animate-spin motion-reduce:animate-none', className)}>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity=".25" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <code className="rounded bg-zinc-950/5 px-1 py-0.5 text-[0.8125rem] dark:bg-white/10">{children}</code>
}

export { focus as focusRing }
