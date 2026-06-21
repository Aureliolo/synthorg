/**
 * Per-endpoint 429 circuit breaker. Verifies the open / close / half-open
 * transitions and key isolation so a single hot endpoint cannot starve the
 * rest, and a tripped breaker self-heals after the cooldown.
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  circuitKeyFromUrl,
  isCircuitOpen,
  recordFailure,
  recordSuccess,
  resetCircuitBreaker,
} from '@/utils/circuit-breaker'

const FAILURE_THRESHOLD = 5
const OPEN_COOLDOWN_MS = 10_000

afterEach(() => {
  resetCircuitBreaker()
  vi.useRealTimers()
})

function failNTimes(key: string, n: number): void {
  for (let i = 0; i < n; i += 1) recordFailure(key)
}

describe('circuit-breaker', () => {
  it('starts closed', () => {
    expect(isCircuitOpen('/providers/presets')).toBe(false)
  })

  it('opens after the consecutive-failure threshold', () => {
    failNTimes('/a', FAILURE_THRESHOLD - 1)
    expect(isCircuitOpen('/a')).toBe(false)
    recordFailure('/a')
    expect(isCircuitOpen('/a')).toBe(true)
  })

  it('a success resets the failure run and closes the breaker', () => {
    failNTimes('/a', FAILURE_THRESHOLD)
    expect(isCircuitOpen('/a')).toBe(true)
    recordSuccess('/a')
    expect(isCircuitOpen('/a')).toBe(false)
    // Failure count was reset, so it takes a full threshold to re-open.
    failNTimes('/a', FAILURE_THRESHOLD - 1)
    expect(isCircuitOpen('/a')).toBe(false)
  })

  it('half-opens (allows a probe) once the cooldown elapses', () => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    failNTimes('/a', FAILURE_THRESHOLD)
    expect(isCircuitOpen('/a')).toBe(true)
    vi.setSystemTime(OPEN_COOLDOWN_MS)
    // Cooldown elapsed: the read clears the open state and lets one probe
    // through.
    expect(isCircuitOpen('/a')).toBe(false)
  })

  it('keys are independent', () => {
    failNTimes('/a', FAILURE_THRESHOLD)
    expect(isCircuitOpen('/a')).toBe(true)
    expect(isCircuitOpen('/b')).toBe(false)
  })

  it('resetCircuitBreaker clears all state', () => {
    failNTimes('/a', FAILURE_THRESHOLD)
    expect(isCircuitOpen('/a')).toBe(true)
    resetCircuitBreaker()
    expect(isCircuitOpen('/a')).toBe(false)
  })
})

describe('circuitKeyFromUrl', () => {
  it('reduces an absolute URL to its path', () => {
    expect(circuitKeyFromUrl('https://host/api/v1/providers/presets?x=1')).toBe(
      '/api/v1/providers/presets',
    )
  })

  it('keeps a relative path as-is', () => {
    expect(circuitKeyFromUrl('/providers/presets')).toBe('/providers/presets')
  })

  it('falls back for an empty url', () => {
    expect(circuitKeyFromUrl(undefined)).toBe('<unknown>')
  })
})
