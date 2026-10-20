// SPDX-License-Identifier: MIT
// Copies the OpenShift build into public/firmware with a manifest, so the
// page can offer it. Run `make -C firmware` first. With --optional (dev
// server), a missing build only prints a warning.
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const src = join(web, '..', 'firmware', 'build', 'openshift.bin')
const out = join(web, 'public', 'firmware')

if (!existsSync(src)) {
  const msg = `${src} not found: run "make -C firmware" first`
  if (process.argv.includes('--optional')) {
    console.warn(`warning: ${msg}; the page will offer no bundled firmware`)
    process.exit(0)
  }
  console.error(`error: ${msg}`)
  process.exit(1)
}

const data = readFileSync(src)
const m = /OpenShift (\d+\.\d+\.\d+[0-9A-Za-z.+-]*)/.exec(data.toString('latin1'))
if (!m) {
  console.error(`error: ${src} has no OpenShift version mark`)
  process.exit(1)
}
const version = m[1]
const file = `openshift-${version}.bin`
// Replaces the files in place: a running dev server stops serving the
// folder if it is deleted and made again.
mkdirSync(out, { recursive: true })
for (const old of readdirSync(out)) {
  if (old !== file && old !== 'manifest.json') rmSync(join(out, old), { force: true })
}
writeFileSync(join(out, file), data)
const manifest = { version, file, size: data.length, sha256: createHash('sha256').update(data).digest('hex') }
writeFileSync(join(out, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
console.log(`public/firmware/${file}: OpenShift ${version}, ${data.length} bytes, sha256 ${manifest.sha256}`)
