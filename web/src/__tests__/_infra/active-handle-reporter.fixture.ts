/**
 * Deliberate-leak fixture for the active-handle tracker regression
 * test (`./active-handle-reporter.test.ts`).
 *
 * Every test in this file intentionally creates a tracked handle and
 * never cleans it up. The tracker (loaded via
 * `web/test-infra/active-handle-reporter.fixture.config.ts`) must
 * throw in `afterEach` and fail each test; the regression test
 * asserts the failures.
 *
 * The `.fixture.ts` extension keeps this file out of the unit
 * project's `*.test.{ts,tsx}` include glob, so the normal
 * `npm run test` invocation does not pick it up. A dedicated config
 * runs it via subprocess.
 */

import { describe, expect, it } from 'vitest'

import {
  leakSetTimeout,
  leakSetInterval,
  leakChainedSetTimeout,
} from './leak-helpers'

describe('active-handle-tracker fail-mode (fixture)', () => {
  it('catches forgotten setTimeout', () => {
    leakSetTimeout()
    expect(true).toBe(true)
  })

  it('catches forgotten setInterval', () => {
    leakSetInterval()
    expect(true).toBe(true)
  })

  it('catches chained-reschedule setTimeout', () => {
    leakChainedSetTimeout()
    expect(true).toBe(true)
  })
})
