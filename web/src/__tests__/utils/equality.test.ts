import { describe, expect, it } from 'vitest'

import { deepEqual } from '@/utils/equality'

/**
 * `deepEqual` exists so a poll that returned identical data skips the store
 * write: writing a fresh object notifies every subscriber and re-renders
 * charts and gauges across the app. That makes its contract "same payload,
 * same answer", and a drift shows up as a re-render storm rather than a
 * type error, so it is worth pinning directly.
 */
describe('deepEqual', () => {
  it('reports identical primitives as equal', () => {
    expect(deepEqual(1, 1)).toBe(true)
    expect(deepEqual('a', 'a')).toBe(true)
    expect(deepEqual(true, true)).toBe(true)
    expect(deepEqual(null, null)).toBe(true)
    expect(deepEqual(undefined, undefined)).toBe(true)
  })

  it('distinguishes null from undefined', () => {
    expect(deepEqual(null, undefined)).toBe(false)
  })

  it('compares nested API-shaped payloads structurally', () => {
    const left = { totals: { spend: 12.5, tasks: 3 }, series: [{ at: 'x', v: 1 }] }
    const right = { totals: { spend: 12.5, tasks: 3 }, series: [{ at: 'x', v: 1 }] }

    expect(deepEqual(left, right)).toBe(true)
  })

  it('reports a differing nested value as unequal', () => {
    const left = { totals: { spend: 12.5, tasks: 3 } }
    const right = { totals: { spend: 12.5, tasks: 4 } }

    expect(deepEqual(left, right)).toBe(false)
  })

  it('ignores key insertion order', () => {
    expect(deepEqual({ a: 1, b: 2 }, { b: 2, a: 1 })).toBe(true)
  })

  it('treats a missing key as different from an undefined value', () => {
    expect(deepEqual({ a: 1 }, { a: 1, b: undefined })).toBe(false)
  })

  it('is order-sensitive for arrays', () => {
    expect(deepEqual([1, 2], [2, 1])).toBe(false)
    expect(deepEqual([1, 2], [1, 2])).toBe(true)
  })

  it('reports arrays of differing length as unequal', () => {
    expect(deepEqual([1, 2], [1, 2, 3])).toBe(false)
  })

  it('does not confuse an array with an object carrying the same indices', () => {
    expect(deepEqual([1, 2], { 0: 1, 1: 2 })).toBe(false)
  })

  it('reports NaN as equal to itself so a poll does not churn on it', () => {
    expect(deepEqual(NaN, NaN)).toBe(true)
    expect(deepEqual({ v: NaN }, { v: NaN })).toBe(true)
  })

  it('does not treat unequal primitives of different types as equal', () => {
    expect(deepEqual(1, '1')).toBe(false)
    expect(deepEqual(0, false)).toBe(false)
    expect(deepEqual('', null)).toBe(false)
  })
})
