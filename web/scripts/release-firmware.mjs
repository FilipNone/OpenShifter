// SPDX-License-Identifier: MIT
// Downloads the firmware from the latest GitHub release into public/firmware
// with a manifest, so the page offers the same file as the release. The file
// must match the release's SHA256SUMS and the SHA-256 GitHub recorded when it
// was uploaded. With --optional (dev server), a failure only prints a warning
// and keeps the files already there.
import { createHash } from 'node:crypto'
import { mkdirSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const web = join(dirname(fileURLToPath(import.meta.url)), '..')
const out = join(web, 'public', 'firmware')
const repo = process.env.GITHUB_REPOSITORY || 'FilipNone/OpenShifter'

async function get(url, headers = {}) {
  const r = await fetch(url, { headers: { 'user-agent': 'openshift-web', ...headers } })
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`)
  return r
}

async function latestRelease() {
  const headers = { accept: 'application/vnd.github+json' }
  // Actions passes a token, which avoids the anonymous rate limit.
  if (process.env.GITHUB_TOKEN) headers.authorization = `Bearer ${process.env.GITHUB_TOKEN}`
  return (await get(`https://api.github.com/repos/${repo}/releases/latest`, headers)).json()
}

async function main() {
  const rel = await latestRelease()
  const bins = rel.assets.filter((a) => /^openshift-.+\.bin$/.test(a.name))
  if (bins.length !== 1) throw new Error(`release ${rel.tag_name} has ${bins.length} openshift-*.bin files, expected 1`)
  const bin = bins[0]
  const sums = rel.assets.find((a) => a.name === 'SHA256SUMS')
  if (!sums) throw new Error(`release ${rel.tag_name} has no SHA256SUMS`)

  const data = Buffer.from(await (await get(bin.browser_download_url)).arrayBuffer())
  const sha256 = createHash('sha256').update(data).digest('hex')
  const listed = (await (await get(sums.browser_download_url)).text()).split('\n')
    .map((l) => /^([0-9a-f]{64}) [ *](.+)$/.exec(l.trim()))
    .find((m) => m && m[2] === bin.name)
  if (!listed) throw new Error(`SHA256SUMS of ${rel.tag_name} does not list ${bin.name}`)
  if (listed[1] !== sha256) throw new Error(`${bin.name} does not match SHA256SUMS of ${rel.tag_name}`)
  if (bin.digest && bin.digest !== `sha256:${sha256}`) {
    throw new Error(`${bin.name} does not match the digest GitHub recorded for it`)
  }

  const m = /OpenShift (\d+\.\d+\.\d+[0-9A-Za-z.+-]*)/.exec(data.toString('latin1'))
  if (!m) throw new Error(`${bin.name} has no OpenShift version mark`)
  const version = m[1]
  if (bin.name !== `openshift-${version}.bin` || rel.tag_name !== `v${version}`) {
    throw new Error(`${bin.name} in release ${rel.tag_name} reports OpenShift ${version}`)
  }

  // Replaces the files in place: a running dev server stops serving the
  // folder if it is deleted and made again. Old files go last, so a failed
  // write never leaves a manifest that names a deleted file.
  mkdirSync(out, { recursive: true })
  writeFileSync(join(out, bin.name), data)
  const manifest = { version, tag: rel.tag_name, file: bin.name, size: data.length, sha256 }
  writeFileSync(join(out, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n')
  for (const old of readdirSync(out)) {
    if (old !== bin.name && old !== 'manifest.json') rmSync(join(out, old), { force: true })
  }
  console.log(`public/firmware/${bin.name}: release ${rel.tag_name}, ${data.length} bytes, sha256 ${sha256}`)
}

main().catch((e) => {
  const msg = e instanceof Error ? e.message : String(e)
  if (process.argv.includes('--optional')) {
    console.warn(`warning: could not get the release firmware (${msg}); the page keeps what public/firmware has`)
    process.exit(0)
  }
  console.error(`error: ${msg}`)
  process.exit(1)
})
