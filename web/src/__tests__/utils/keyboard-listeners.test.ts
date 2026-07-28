import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'

/**
 * Every global keydown listener must survive an event with no `key`.
 *
 * The crash this guards is invisible to TypeScript, which types
 * `KeyboardEvent.key` as `string` and so cannot see that browser autofill and
 * synthetic library events dispatch without one. A per-component test only
 * covers the components someone remembered, and the bug reached production
 * through the one that was missed, so the check is over the whole tree.
 */

// Vitest runs with the dashboard package as its working directory.
const SRC = join(process.cwd(), 'src')

// Every path below descends from SRC, which is derived from the runner's own
// working directory; nothing here takes a caller-supplied component.
function sourceFiles(dir: string): string[] {
  const found: string[] = []
  // eslint-disable-next-line security/detect-non-literal-fs-filename
  for (const entry of readdirSync(dir)) {
    if (entry === '__tests__' || entry === 'node_modules') continue
    const full = join(dir, entry)
    // eslint-disable-next-line security/detect-non-literal-fs-filename
    if (statSync(full).isDirectory()) {
      found.push(...sourceFiles(full))
    } else if (/\.tsx?$/.test(entry)) {
      found.push(full)
    }
  }
  return found
}

describe('keydown listeners', () => {
  it('never call .toLowerCase() directly on event.key', () => {
    const offenders = sourceFiles(SRC).filter((file) => {
      // `src/utils/keyboard.ts` is the one place allowed to touch it,
      // behind the optional chain every other call site routes through.
      // Matched as a whole path, not a suffix: a `keyboard.ts` under any
      // other `utils/` directory would otherwise exempt itself.
      const relative = file.replace(/\\/g, '/').slice(SRC.replace(/\\/g, '/').length)
      if (relative === '/utils/keyboard.ts') return false
      // eslint-disable-next-line security/detect-non-literal-fs-filename
      const source = readFileSync(file, 'utf8')
      return /\.key\s*\.\s*to(Lower|Upper)Case\(/.test(source)
    })

    expect(offenders).toEqual([])
  })
})
