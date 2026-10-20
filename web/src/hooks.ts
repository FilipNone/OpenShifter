// SPDX-License-Identifier: MIT
import { useEffect, useState } from 'react'
import type { Inputs, Lever, Status } from './lib/protocol'
import type { Session } from './lib/session'

export interface Snapshot {
  status: Status | null
  inputs: Inputs
  /** The lever averaged over the last few reports. */
  lever: Lever | null
}

/** The session's latest reports, sampled `hz` times a second. The shifter
 * sends both reports every 2 ms; rendering each would waste the CPU. */
export function useSnapshot(session: Session | null, hz: number): Snapshot {
  const [snap, setSnap] = useState<Snapshot>(() => ({ status: session?.status ?? null,
    inputs: session?.inputs ?? { gears: 0, buttons: 0, handbrake: 0 }, lever: session?.lever ?? null }))
  useEffect(() => {
    if (!session) return
    let last: Status | null = null
    let lastInputs: Inputs | null = null
    let lastLever: Lever | null = null
    const tick = () => {
      if (session.status !== last || session.inputs !== lastInputs) {
        const lever = session.lever
        const changed = !last || !session.status || !same(last.raw, session.status.raw) ||
          !lastInputs || !sameInputs(lastInputs, session.inputs) || !sameLever(lastLever, lever)
        last = session.status
        lastInputs = session.inputs
        lastLever = lever
        if (changed) setSnap({ status: session.status, inputs: session.inputs, lever })
      }
    }
    tick()
    const t = setInterval(tick, 1000 / hz)
    return () => clearInterval(t)
  }, [session, hz])
  return snap
}

function same(a: Uint8Array, b: Uint8Array) {
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false
  return true
}

function sameLever(a: Lever | null, b: Lever | null) {
  return a === b || (!!a && !!b && a.x === b.x && a.y === b.y && a.radius === b.radius)
}

function sameInputs(a: Inputs, b: Inputs) {
  return a.gears === b.gears && a.buttons === b.buttons && a.handbrake === b.handbrake
}
