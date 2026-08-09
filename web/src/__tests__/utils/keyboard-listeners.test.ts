import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

/**
 * Every global keydown listener must survive an event with no `key`.
 *
 * The crash this guards is invisible to TypeScript, which types
 * `KeyboardEvent.key` as `string` and so cannot see that browser autofill and
 * synthetic library events dispatch without one. A per-component test only
 * covers the components someone remembered, and the bug reached production
 * through the one that was missed, so the check is over the whole tree.
 */

// Anchored on this file, not on the runner's working directory: invoked as
// `vitest --root web` from the repository root, a cwd-relative `src` resolves
// to the Python package instead and the scan passes having read nothing.
const WEB_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')

/**
 * Every tracked `src/` module, tests aside.
 *
 * The subject is the source that can reach main, so the file list comes from
 * the index rather than from a directory walk. Sibling suites materialise
 * `.ts` fixtures inside `src/` for the span of one file and delete them again,
 * and a walk running in another worker hits those between `readdir` and
 * `read`: with one such suite churning, a measured 70% of walks died on ENOENT.
 * A tracked path cannot appear and vanish underneath the read.
 */
function trackedSourceFiles(): string[] {
  const listed = spawnSync('git', ['ls-files', '-z', '--cached', '--', 'src'], {
    cwd: WEB_ROOT,
    encoding: 'utf8',
  })
  if (listed.status !== 0) {
    throw new Error(
      `git ls-files failed (status=${String(listed.status)}): ${listed.stderr}`,
    )
  }
  return listed.stdout
    .split('\0')
    .filter((path) => /\.tsx?$/.test(path) && !path.includes('/__tests__/'))
}

describe('keydown listeners', () => {
  it('never call .toLowerCase() directly on event.key', () => {
    const files = trackedSourceFiles()
    // A silently empty list would pass for the wrong reason.
    expect(files.length).toBeGreaterThan(100)

    // No file is exempt, `src/utils/keyboard.ts` included: the pattern matches
    // only the unguarded form, and the shared helper reaches the same call
    // through the optional chain (`event.key?.toLowerCase()`) that every other
    // site routes through. An exemption there would whitelist this very crash
    // in the one module whose whole job is to prevent it.
    const offenders = files.filter((path) => {
      // eslint-disable-next-line security/detect-non-literal-fs-filename
      const source = readFileSync(join(WEB_ROOT, path), 'utf8')
      return /\.key\s*\.\s*to(Lower|Upper)Case\(/.test(source)
    })

    expect(offenders).toEqual([])
  })
})
