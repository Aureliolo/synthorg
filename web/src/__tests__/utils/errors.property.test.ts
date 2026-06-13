import fc from 'fast-check'
import { vi } from 'vitest'
import { getErrorMessage } from '@/utils/errors'

// Honour ``FC_SEED`` so a CI failure can be reproduced locally:
// ``FC_SEED=<n> npm --prefix web run test -- errors.property``.
// Unset, fast-check picks its own seed each run (the default).
const parsedSeed = Number.parseInt(process.env['FC_SEED'] ?? '', 10)
const FC_SEED = Number.isFinite(parsedSeed) ? parsedSeed : undefined

// Mock axios so the helper's classification logic is tested in
// isolation: this property-test file only cares about
// ``getErrorMessage``'s contract, not axios's adapter detection.
vi.mock('axios', () => ({
  default: {
    isAxiosError: (err: unknown) =>
      typeof err === 'object' && err !== null && (err as Record<string, unknown>)['isAxiosError'] === true,
  },
  isAxiosError: (err: unknown) =>
    typeof err === 'object' && err !== null && (err as Record<string, unknown>)['isAxiosError'] === true,
}))

/** Build a fake AxiosError-shaped object without importing the real class. */
function makeFakeAxiosError(status: number, data: unknown) {
  const err = new Error('Request failed') as Error & {
    isAxiosError: boolean
    response: { status: number; data: unknown }
  }
  err.isAxiosError = true
  err.response = { status, data }
  return err
}

describe('errors property tests', () => {
  it('getErrorMessage never returns empty string', () => {
    fc.assert(
      fc.property(fc.anything(), (input) => {
        const msg = getErrorMessage(input)
        expect(msg.length).toBeGreaterThan(0)
      }),
      { seed: FC_SEED },
    )
  })

  it('getErrorMessage for 5xx never leaks response body', () => {
    const statusArb = fc.integer({ min: 500, max: 599 })
    // Use identifiable strings that would never appear in generic messages
    const bodyArb = fc.stringMatching(/^[A-Z][a-z]{4,20}Error: .{5,50}$/)

    fc.assert(
      fc.property(statusArb, bodyArb, (status, body) => {
        const error = makeFakeAxiosError(status, { error: body })
        const msg = getErrorMessage(error)
        expect(msg).not.toContain(body)
      }),
      { seed: FC_SEED },
    )
  })
})
