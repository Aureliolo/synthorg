import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * Regression test for #2072. The cycle
 *   stores/auth -> api/endpoints/auth -> api/client -> stores/auth
 * was previously suppressed via `dpdm --skip-imports`. This file fails the
 * suite if either (a) the suppression is reintroduced or (b) a new edge
 * recreates a circular dependency reachable from `src/main.tsx`.
 */

const WEB_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../..',
)

// Invoke dpdm's JS entrypoint directly via `node`. Cross-platform and avoids
// the Node.js DEP0190 deprecation that fires when `spawnSync` uses
// `shell: true` (the only other way to run the `dpdm.cmd` shim on Windows).
const DPDM_SCRIPT = path.join(WEB_ROOT, 'node_modules', 'dpdm', 'lib', 'bin', 'dpdm.js')

describe('module graph integrity', () => {
  it('package.json lint:circular has no --skip-imports exemption', () => {
    const pkg = JSON.parse(
      readFileSync(path.join(WEB_ROOT, 'package.json'), 'utf8'),
    ) as { scripts: Record<string, string> }
    expect(pkg.scripts['lint:circular']).toBeDefined()
    expect(pkg.scripts['lint:circular']).not.toContain('--skip-imports')
  })

  it('dpdm reports no cycles reachable from src/main.tsx', () => {
    const result = spawnSync(
      process.execPath,
      [
        DPDM_SCRIPT,
        '--no-warning',
        '--no-tree',
        '--exit-code',
        'circular:1',
        'src/main.tsx',
      ],
      { cwd: WEB_ROOT, encoding: 'utf8', timeout: 60_000 },
    )
    if (result.status !== 0) {
      throw new Error(
        `dpdm exited with status ${String(result.status)} (signal=${String(result.signal)}):\n` +
          `stdout:\n${result.stdout}\nstderr:\n${result.stderr}\n` +
          `error: ${result.error?.message ?? '(none)'}`,
      )
    }
  }, 60_000)
})
