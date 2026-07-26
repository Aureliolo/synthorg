import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { ESLint } from 'eslint'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

/**
 * Regression guard for the "one barrel per name" convention gate.
 *
 * The rules enforcing it are esquery selectors and import globs, i.e.
 * stringly-typed. A typo turns one into a matcher that never fires, and since a
 * clean tree has no violations either way, nothing else in the suite would
 * notice: the gate would keep reporting success while enforcing nothing. Each
 * case below pairs a shape that MUST be rejected with the neighbouring
 * legitimate shape that MUST stay accepted, so an over-broad rule fails here
 * too.
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
    id: 'generated-module-import',
    file: 'src/utils/__barrel_probe_b__.ts',
    code: "import type { HumanRole } from '@/api/types/enum-values.gen'\nexport type P = HumanRole\n",
  },
  {
    id: 'generated-module-import-inside-types',
    file: 'src/api/types/__barrel_probe_c__.ts',
    code: "export type { HumanRole } from './enum-values.gen'\n",
  },
  {
    id: 'wildcard-reexport-of-generated',
    file: 'src/api/types/__barrel_probe_d__.ts',
    code: "export * from './dtos.gen'\n",
  },
  {
    id: 'sibling-domain-reexport',
    file: 'src/api/types/__barrel_probe_e__.ts',
    code: "export type { RunOutcome } from './enums'\n",
  },
  {
    // The selector originally matched `[a-z-]+`, so a module name carrying a
    // digit or a capital would have walked straight past it.
    id: 'sibling-domain-reexport-awkward-name',
    file: 'src/api/types/__barrel_probe_e2__.ts',
    code: "export type { RunOutcome } from './enums2'\n",
  },
  {
    id: 'endpoint-dto-passthrough',
    file: 'src/api/endpoints/__barrel_probe_f__.ts',
    code: "export type { AgentConfig } from '@/api/types/agents'\n",
  },
  {
    id: 'endpoint-derived-type',
    file: 'src/api/endpoints/__barrel_probe_g__.ts',
    code:
      "import type { ReviewStageResult } from '@/api/types/clients'\n"
      + "export type StageVerdict = ReviewStageResult['verdict']\n",
  },
  {
    id: 'duplicate-same-kind-import',
    file: 'src/stores/__barrel_probe_h__.ts',
    code:
      "import type { AgentConfig } from '@/api/types/agents'\n"
      + "import type { CareerEvent } from '@/api/types/agents'\n"
      + 'export type P = AgentConfig | CareerEvent\n',
  },
  {
    id: 'separate-type-and-value-import',
    file: 'src/stores/__barrel_probe_i__.ts',
    code:
      "import { AGENT_STATUS_VALUES } from '@/api/types/enums'\n"
      + "import type { AgentStatus } from '@/api/types/enums'\n"
      + 'export const values = AGENT_STATUS_VALUES\n'
      + 'export type P = AgentStatus\n',
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

  it('rejects reaching past a barrel into a generated module', () => {
    expect(rulesFor('generated-module-import')).toContain('no-restricted-imports')
  })

  it('lets api/types itself read the generated modules', () => {
    expect(rulesFor('generated-module-import-inside-types')).not.toContain(
      'no-restricted-imports',
    )
  })

  it('rejects a wildcard re-export of a generated module', () => {
    expect(rulesFor('wildcard-reexport-of-generated')).toContain('no-restricted-syntax')
  })

  it('rejects a domain module re-exporting from a sibling domain module', () => {
    expect(rulesFor('sibling-domain-reexport')).toContain('no-restricted-syntax')
  })

  it('rejects a sibling re-export whose module name has a digit', () => {
    expect(rulesFor('sibling-domain-reexport-awkward-name')).toContain(
      'no-restricted-syntax',
    )
  })

  it('rejects a DTO pass-through re-export from an endpoint module', () => {
    expect(rulesFor('endpoint-dto-passthrough')).toContain('no-restricted-syntax')
  })

  it('lets an endpoint module export a type it derives', () => {
    expect(rulesFor('endpoint-derived-type')).not.toContain('no-restricted-syntax')
  })

  it('rejects two same-kind imports of one module', () => {
    expect(rulesFor('duplicate-same-kind-import')).toContain('no-duplicate-imports')
  })

  it('allows the deliberate type/value import split', () => {
    expect(rulesFor('separate-type-and-value-import')).not.toContain(
      'no-duplicate-imports',
    )
  })
})
