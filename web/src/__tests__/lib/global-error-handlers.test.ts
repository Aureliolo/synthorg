import { isBenignError } from '@/lib/global-error-handlers'

describe('isBenignError', () => {
  it.each([
    'ResizeObserver loop completed with undelivered notifications.',
    'ResizeObserver loop limit exceeded',
    'Hydration failed because the initial UI does not match what was rendered on the server.',
    'Text content does not match server-rendered HTML.',
    'Hydration completed but contains mismatches.',
    'Loading chunk 42 failed.',
    'Failed to fetch dynamically imported module: https://example.com/chunk.js',
  ])('treats %j as benign', (message) => {
    expect(isBenignError(message)).toBe(true)
    expect(isBenignError(new Error(message))).toBe(true)
  })

  it('does not match arbitrary errors', () => {
    expect(isBenignError('Some other failure')).toBe(false)
    expect(isBenignError(new TypeError('cannot read properties of undefined'))).toBe(
      false,
    )
    expect(isBenignError({ message: 'shaped like an Error but not one' })).toBe(false)
  })

  it.each([
    'ResizeObserver loop completed and now the server is on fire',
    'ResizeObserver loop in the order service crashed during checkout',
    'Loading chunk 5 failed because the auth service refused our token',
    'Failed to fetch dynamically imported module and also the database',
  ])('does not match backend errors that just happen to share a prefix: %j', (message) => {
    expect(isBenignError(message)).toBe(false)
  })

  it('returns false for non-string non-Error inputs', () => {
    expect(isBenignError(null)).toBe(false)
    expect(isBenignError(undefined)).toBe(false)
    expect(isBenignError(42)).toBe(false)
    expect(isBenignError({})).toBe(false)
  })
})
