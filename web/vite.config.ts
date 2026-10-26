/// <reference types="vitest/config" />
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { appendFileSync, readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { defineConfig, type Plugin } from 'vite'

const LICENSES = 'THIRD-PARTY-LICENSES.txt'

// Vite's license file covers the packages bundled into the JavaScript. The
// font and the Tailwind CSS come in through the stylesheet, so their license
// texts are added here, in the same format.
function cssLicenses(packages: string[]): Plugin {
  const require = createRequire(import.meta.url)
  let outDir = ''
  return {
    name: 'css-licenses',
    apply: 'build',
    configResolved(c) { outDir = c.build.outDir },
    writeBundle() {
      const text = packages.map((name) => {
        const dir = dirname(require.resolve(`${name}/package.json`))
        const pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'))
        return `\n## ${name} - ${pkg.version} (${pkg.license})\n\n${readFileSync(join(dir, 'LICENSE'), 'utf8').trim()}\n`
      })
      appendFileSync(join(outDir, LICENSES), text.join(''))
    },
  }
}

// A relative base, so the build works at any GitHub Pages path. The footer
// links to the license file.
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss(), cssLicenses(['@fontsource-variable/inter', 'tailwindcss'])],
  build: { target: 'es2022', sourcemap: true, license: { fileName: LICENSES } },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
