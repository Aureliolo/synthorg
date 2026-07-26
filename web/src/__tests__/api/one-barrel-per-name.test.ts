import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ESLint } from 'eslint'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

/**
 * Regression guard for the "one barrel per name" convention gate.
 *
 * The rule enforcing it is an import glob, i.e. stringly-typed. A typo turns it
 * into a matcher that never fires, and since a clean tree has no violations
 * either way, nothing else in the suite would notice: the gate would keep
 * reporting success while enforcing nothing. Each case below pairs a shape that
 * MUST be rejected with the neighbouring legitimate shape that MUST stay
 * accepted, so an over-broad rule fails here too.
 *
 * Fixtures are written to real paths under `src/` because the config lints with
 * type information, and `projectService` resolves a file only if it exists on
 * disk inside a tsconfig include glob. They are removed again in `afterAll`.
 */

const WEB_ROOT = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../..',
)

interface Fixture {
  readonly id: string
  readonly file: string
  readonly code: string
}

const FIXTURES = [
  {
    id: 'index-barrel-import',
    file: 'src/pages/__barrel_probe_a__.ts',
    code: "import type { AgentConfig } from '@/api/types'\nexport type P = AgentConfig\n",
  },
  {
    id: 'domain-barrel-import',
    file: 'src/pages/__barrel_probe_b__.ts',
    code:
      "import type { AgentConfig } from '@/api/types/agents'\n"
      + 'export type P = AgentConfig\n',
  },
] as const satisfies readonly Fixture[]

/** fixture id -> rule ids ESLint reported for it. */
const reported = new Map<string, readonly string[]>()

beforeAll(async () => {
  for (const f of FIXTURES) {
    // Paths come from the frozen FIXTURES list above joined onto WEB_ROOT, so
    // there is no caller-supplied component for the rule to be warning about.
    // eslint-disable-next-line security/detect-non-literal-fs-filename
    fs.writeFileSync(path.join(WEB_ROOT, f.file), f.code, 'utf8')
  }
  // One ESLint run for every fixture: `projectService` startup dominates the
  // cost, so per-case runs would blow the suite's per-test timeout.
  const eslint = new ESLint({ cwd: WEB_ROOT })
  const results = await eslint.lintFiles(
    FIXTURES.map((f) => path.join(WEB_ROOT, f.file)),
  )
  const byPath = new Map(
    results.map((r) => [
      path.relative(WEB_ROOT, r.filePath).split(path.sep).join('/'),
      r.messages.map((m) => m.ruleId ?? `(fatal: ${m.message})`),
    ]),
  )
  for (const f of FIXTURES) reported.set(f.id, byPath.get(f.file) ?? [])
}, 120_000)

afterAll(() => {
  for (const f of FIXTURES) {
    fs.rmSync(path.join(WEB_ROOT, f.file), { force: true })
  }
})

/** Guards against a fixture silently failing to parse, which would make every
 *  `not.toContain` assertion below pass for the wrong reason. */
function rulesFor(id: string): readonly string[] {
  const ids = reported.get(id) ?? []
  const fatal = ids.find((r) => r.startsWith('(fatal'))
  if (fatal !== undefined) throw new Error(`fixture ${id} did not lint: ${fatal}`)
  return ids
}

describe('one barrel per name', () => {
  it('rejects an import from the removed index barrel', () => {
    expect(rulesFor('index-barrel-import')).toContain('no-restricted-imports')
  })

  it('leaves a domain barrel import alone', () => {
    expect(rulesFor('domain-barrel-import')).not.toContain('no-restricted-imports')
  })
})
