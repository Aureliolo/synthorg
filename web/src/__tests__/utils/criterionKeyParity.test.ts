import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

import { coverageKey } from '@/utils/planCoverage'

/**
 * The dashboard half of the criterion-key parity fixture.
 *
 * `coverageKey` is a second implementation of the backend's
 * `synthorg.core.criterion_match.criterion_key`, and nothing but this fixture
 * ties them together. The sibling test is
 * `tests/unit/core/test_criterion_key_parity.py`; both key every case in
 * `data/criterion_key_cases.json`. Looser here places a claim the backend
 * rejected; stricter leaves one it accepted reading as uncovered.
 */
interface ParityCase {
  readonly text: string
  readonly key: string
}

// A literal path, relative to vitest's own root (`web/`), so the fixture both
// halves read is named the same way in both and nothing computes it.
const fixture = JSON.parse(
  readFileSync('../data/criterion_key_cases.json', 'utf8'),
) as { readonly cases: readonly ParityCase[] }

describe('criterion key parity', () => {
  it('carries cases, so an empty fixture cannot pass silently', () => {
    expect(fixture.cases.length).toBeGreaterThanOrEqual(8)
  })

  it.each(fixture.cases)('keys $text the way the backend must', ({ text, key }) => {
    expect(coverageKey(text)).toBe(key)
  })
})
