// SPDX-License-Identifier: MIT
// The part of WebHID the app uses, so the simulated shifter can stand in for
// a real HIDDevice.

export interface InputReport {
  reportId: number
  data: DataView
}

export interface ShifterDevice {
  readonly productId: number
  readonly productName: string
  readonly opened: boolean
  readonly collections: readonly HIDCollectionInfo[]
  open(): Promise<void>
  close(): Promise<void>
  sendReport(reportId: number, data: Uint8Array): Promise<void>
  receiveFeatureReport(reportId: number): Promise<DataView>
  addEventListener(type: 'inputreport', listener: (e: InputReport) => void): void
  removeEventListener(type: 'inputreport', listener: (e: InputReport) => void): void
}

/** Where shifters come from: granted devices, the chooser, and a clock. */
export interface Bus {
  /** Connected devices this page already has permission for. */
  devices(): Promise<ShifterDevice[]>
  /** Shows the browser's chooser; needs a user gesture. Null if cancelled. */
  request(productIds: number[]): Promise<ShifterDevice | null>
  onDisconnect(listener: (d: ShifterDevice) => void): () => void
  /** A granted device was plugged in. */
  onConnect(listener: (d: ShifterDevice) => void): () => void
  sleep(ms: number): Promise<void>
  now(): number
  readonly simulated: boolean
}

export function webHidAvailable(): boolean {
  return typeof navigator !== 'undefined' && 'hid' in navigator && window.isSecureContext
}

export function webHidBus(vendorId: number): Bus {
  const hid = navigator.hid
  return {
    simulated: false,
    async devices() {
      return (await hid.getDevices()).filter((d) => d.vendorId === vendorId) as unknown as ShifterDevice[]
    },
    async request(productIds) {
      const found = await hid.requestDevice({ filters: productIds.map((productId) => ({ vendorId, productId })) })
      return (found[0] as unknown as ShifterDevice) ?? null
    },
    onDisconnect(listener) {
      const h = (e: HIDConnectionEvent) => listener(e.device as unknown as ShifterDevice)
      hid.addEventListener('disconnect', h)
      return () => hid.removeEventListener('disconnect', h)
    },
    onConnect(listener) {
      const h = (e: HIDConnectionEvent) => { if (e.device.vendorId === vendorId) listener(e.device as unknown as ShifterDevice) }
      hid.addEventListener('connect', h)
      return () => hid.removeEventListener('connect', h)
    },
    sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
    now: () => performance.now(),
  }
}

export function reportBytes(r: HIDReportInfo): number {
  let bits = 0
  for (const item of r.items ?? []) bits += (item.reportSize ?? 0) * (item.reportCount ?? 0)
  return bits / 8
}

export function outputReports(collections: readonly HIDCollectionInfo[] | undefined): HIDReportInfo[] {
  const out: HIDReportInfo[] = []
  for (const c of collections ?? []) {
    out.push(...(c.outputReports ?? []))
    out.push(...outputReports(c.children))
  }
  return out
}
