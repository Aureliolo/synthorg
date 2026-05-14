/**
 * Regression suite for the active-handle tracker / reporter pair.
 *
 * Two layers:
 *
 *   1. Pure-function tests for the classification primitives
 *      (`findUserFrame`, `isUserFrame`, `matchAllowlist`). These are
 *      cheap, deterministic, and lock in the rules that decide what
 *      counts as a user leak.
 *
 *   2. End-to-end subprocess tests that spawn `vitest run` against a
 *      sibling fixture file that deliberately leaks one resource per
 *      test. The parent test asserts the child exits non-zero AND
 *      the stderr names each fixture test and the resource type. If
 *      the tracker ever silently passes a real leak through, layer 2
 *      catches it; if the classification rules drift, layer 1 catches
 *      it.
 */

import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { dirname, join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

import {
  findUserFrame,
  isUserFrame,
  matchAllowlist,
} from '../../../test-infra/active-handle-tracker'

const HERE = dirname(fileURLToPath(import.meta.url))
const WEB_ROOT = resolve(HERE, '../../..')

describe('isUserFrame', () => {
  it('accepts a frame rooted in web/src', () => {
    expect(
      isUserFrame(
        'at handleSubmit (C:/repo/web/src/pages/foo/FooPage.tsx:42:7)',
      ),
    ).toBe(true)
  })

  it('rejects node_modules frames', () => {
    expect(
      isUserFrame(
        'at intercept (C:/repo/web/node_modules/msw/dist/index.js:99:1)',
      ),
    ).toBe(false)
  })

  it('rejects vitest internals', () => {
    expect(
      isUserFrame(
        'at runTest (C:/repo/web/node_modules/vitest/dist/runner.js:1:1)',
      ),
    ).toBe(false)
  })

  it('rejects test-infra frames', () => {
    expect(
      isUserFrame(
        'at beforeEach (C:/repo/web/test-infra/active-handle-tracker.ts:1:1)',
      ),
    ).toBe(false)
  })

  it('rejects node: internal frames', () => {
    expect(isUserFrame('at node:internal/timers:1:1')).toBe(false)
  })

  it('rejects frames outside web/src even if not in node_modules', () => {
    expect(
      isUserFrame('at someFn (C:/repo/web/other-dir/file.ts:1:1)'),
    ).toBe(false)
  })
})

describe('findUserFrame', () => {
  it('returns null when no frame reaches web/src', () => {
    const stack = [
      'Error',
      '    at intercept (C:/repo/web/node_modules/msw/dist/index.js:1:1)',
      '    at node:internal/timers:99:1',
    ].join('\n')
    expect(findUserFrame(stack)).toBeNull()
  })

  it('returns the first web/src frame it encounters', () => {
    const stack = [
      'Error',
      '    at intercept (C:/repo/web/node_modules/msw/dist/index.js:1:1)',
      '    at handleSubmit (C:/repo/web/src/pages/foo/FooPage.tsx:42:7)',
      '    at someOuter (C:/repo/web/src/pages/foo/wrapper.tsx:10:1)',
    ].join('\n')
    const frame = findUserFrame(stack)
    expect(frame).not.toBeNull()
    expect(frame).toContain('FooPage.tsx:42:7')
  })

  it('skips test-infra frames even when web/src is in the path', () => {
    const stack = [
      'Error',
      '    at init (C:/repo/web/test-infra/active-handle-tracker.ts:1:1)',
      '    at handle (C:/repo/web/src/pages/foo/FooPage.tsx:5:1)',
    ].join('\n')
    expect(findUserFrame(stack)).toContain('FooPage.tsx:5:1')
  })
})

describe('matchAllowlist', () => {
  it('returns null on an empty allowlist', () => {
    expect(matchAllowlist('Timeout', 'any stack', [])).toBeNull()
  })

  it('returns the entry when both type and frame match', () => {
    const entry = {
      type: 'Timeout',
      framePattern: /jsdom-internal/,
      reason: 'structural floor',
    }
    expect(
      matchAllowlist('Timeout', 'at jsdom-internal-thing', [entry]),
    ).toBe(entry)
  })

  it('returns null when type matches but frame does not', () => {
    const entry = {
      type: 'Timeout',
      framePattern: /jsdom-internal/,
      reason: 'structural floor',
    }
    expect(
      matchAllowlist('Timeout', 'at user-code', [entry]),
    ).toBeNull()
  })

  it('returns null when frame matches but type does not', () => {
    const entry = {
      type: 'TCPWRAP',
      framePattern: /jsdom-internal/,
      reason: 'structural floor',
    }
    expect(
      matchAllowlist('Timeout', 'at jsdom-internal', [entry]),
    ).toBeNull()
  })
})

interface FixtureCase {
  /** Substring expected to appear in the test name. */
  testName: string
  /** Resource type the tracker must name in its error message. */
  resourceType: string
}

const FIXTURE_CASES: readonly FixtureCase[] = [
  { testName: 'catches forgotten setTimeout', resourceType: 'Timeout' },
  { testName: 'catches forgotten setInterval', resourceType: 'Timeout' },
  {
    testName: 'catches chained-reschedule setTimeout',
    resourceType: 'Timeout',
  },
]

describe('end-to-end: fail-mode catches deliberate leaks', () => {
  it('child vitest run fails and surfaces every leak type', () => {
    // Isolate the child's NDJSON leak log so the parent's main
    // reporter does not double-count fixture leaks during the
    // parent's own ``onTestRunEnd``.
    const childLogDir = join(WEB_ROOT, '.test-tmp', 'fixture-child')
    const result = spawnSync(
      process.execPath,
      [
        join(WEB_ROOT, 'node_modules', 'vitest', 'vitest.mjs'),
        'run',
        '--config',
        join(WEB_ROOT, 'test-infra', 'active-handle-reporter.fixture.config.ts'),
      ],
      {
        cwd: WEB_ROOT,
        env: {
          ...process.env,
          NO_COLOR: '1',
          ACTIVE_HANDLE_MODE: 'fail',
          ACTIVE_HANDLE_LOG_DIR: childLogDir,
        },
        encoding: 'utf8',
        timeout: 60_000,
      },
    )
    const combined = `${result.stdout ?? ''}\n${result.stderr ?? ''}`

    // The child run must FAIL: every fixture test deliberately leaks
    // a tracked handle, and fail-mode throws in afterEach.
    expect(result.status, combined).not.toBe(0)

    for (const fixture of FIXTURE_CASES) {
      expect(
        combined,
        `fixture "${fixture.testName}" should be named in the child output`,
      ).toContain(fixture.testName)
      // eslint-disable-next-line security/detect-non-literal-regexp -- inputs are statically-known fixture constants
      const pattern = new RegExp(
        `${fixture.resourceType}[\\s\\S]{0,200}${escapeRegex(fixture.testName)}`,
        'i',
      )
      expect(
        combined,
        `tracker should report "${fixture.resourceType}" for fixture "${fixture.testName}"`,
      ).toMatch(pattern)
    }

    // The tracker's branded prefix must appear so we know the error
    // came from our throw path, not some unrelated child failure.
    expect(combined).toContain('[active-handle-tracker]')
  }, 120_000)
})

function escapeRegex(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
