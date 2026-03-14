import { describe, it, expect, vi } from 'vitest'
import fc from 'fast-check'

// Mock axios before importing the module under test
vi.mock('axios', () => ({
  default: {
    isAxiosError: (err: unknown): boolean =>
      typeof err === 'object' &&
      err !== null &&
      'isAxiosError' in err &&
      (err as { isAxiosError?: unknown }).isAxiosError === true,
  },
  isAxiosError: (err: unknown): boolean =>
    typeof err === 'object' &&
    err !== null &&
    'isAxiosError' in err &&
    (err as { isAxiosError?: unknown }).isAxiosError === true,
}))

import { getErrorMessage } from '@/utils/errors'

describe('getErrorMessage (property-based)', () => {
  it('always returns a non-empty string for any input', () => {
    fc.assert(
      fc.property(fc.anything(), (input) => {
        const result = getErrorMessage(input)
        expect(typeof result).toBe('string')
        expect(result.length).toBeGreaterThan(0)
      }),
    )
  })

  it('never throws on any input', () => {
    fc.assert(
      fc.property(fc.anything(), (input) => {
        expect(() => getErrorMessage(input)).not.toThrow()
      }),
    )
  })

  it('returns a string for Error objects with arbitrary messages', () => {
    fc.assert(
      fc.property(fc.string(), (msg) => {
        const err = new Error(msg)
        const result = getErrorMessage(err)
        expect(typeof result).toBe('string')
        expect(result.length).toBeGreaterThan(0)
      }),
    )
  })

  it('returns generic message for Errors with long messages (>= 200 chars)', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 200, maxLength: 500 }),
        (msg) => {
          const err = new Error(msg)
          const result = getErrorMessage(err)
          expect(result).toBe('An unexpected error occurred.')
        },
      ),
    )
  })

  it('returns generic message for Errors whose message starts with "{"', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 100 }),
        (suffix) => {
          const err = new Error(`{${suffix}`)
          const result = getErrorMessage(err)
          expect(result).toBe('An unexpected error occurred.')
        },
      ),
    )
  })

  it('returns the Error message when short and not JSON-like', () => {
    fc.assert(
      fc.property(
        fc.string({ minLength: 1, maxLength: 199 }).filter((s) => !s.startsWith('{')),
        (msg) => {
          const err = new Error(msg)
          const result = getErrorMessage(err)
          expect(result).toBe(msg)
        },
      ),
    )
  })

  it('returns generic message for Error with empty message', () => {
    const err = new Error('')
    const result = getErrorMessage(err)
    expect(result).toBe('An unexpected error occurred.')
  })

  it('returns generic message for non-Error, non-Axios values', () => {
    fc.assert(
      fc.property(
        fc.oneof(fc.integer(), fc.boolean(), fc.constant(null), fc.constant(undefined)),
        (input) => {
          const result = getErrorMessage(input)
          expect(result).toBe('An unexpected error occurred.')
        },
      ),
    )
  })
})
