import { describe, it, expect } from 'vitest'
import fc from 'fast-check'
import { sanitizeForLog } from '@/utils/logging'

/** Check whether String(value) will succeed without throwing. */
function isStringifiable(value: unknown): boolean {
  try {
    String(value)
    return true
  } catch {
    return false
  }
}

/**
 * Arbitrary that generates any value that can be safely converted via String().
 * Objects with a non-callable toString/valueOf cause String() to throw TypeError,
 * so we filter those out.
 */
const safeAnything = fc.anything().filter(isStringifiable)

describe('sanitizeForLog (property-based)', () => {
  it('never contains control characters (code < 0x20) in output', () => {
    fc.assert(
      fc.property(safeAnything, (input) => {
        const result = sanitizeForLog(input)
        for (const ch of result) {
          const code = ch.charCodeAt(0)
          expect(code).toBeGreaterThanOrEqual(0x20)
        }
      }),
    )
  })

  it('never contains DEL (0x7F) in output', () => {
    fc.assert(
      fc.property(safeAnything, (input) => {
        const result = sanitizeForLog(input)
        for (const ch of result) {
          expect(ch.charCodeAt(0)).not.toBe(0x7f)
        }
      }),
    )
  })

  it('output length never exceeds default maxLen of 500', () => {
    fc.assert(
      fc.property(safeAnything, (input) => {
        const result = sanitizeForLog(input)
        expect(result.length).toBeLessThanOrEqual(500)
      }),
    )
  })

  it('output length never exceeds a custom maxLen', () => {
    fc.assert(
      fc.property(
        safeAnything,
        fc.integer({ min: 1, max: 2000 }),
        (input, maxLen) => {
          const result = sanitizeForLog(input, maxLen)
          expect(result.length).toBeLessThanOrEqual(maxLen)
        },
      ),
    )
  })

  it('never throws on stringifiable input', () => {
    fc.assert(
      fc.property(safeAnything, (input) => {
        expect(() => sanitizeForLog(input)).not.toThrow()
      }),
    )
  })

  it('always returns a string', () => {
    fc.assert(
      fc.property(safeAnything, (input) => {
        expect(typeof sanitizeForLog(input)).toBe('string')
      }),
    )
  })

  it('preserves printable ASCII characters unchanged', () => {
    // Generate strings with only printable ASCII (0x20-0x7E)
    const printableAscii = fc.string({
      unit: fc.integer({ min: 0x20, max: 0x7e }).map((code) => String.fromCharCode(code)),
      maxLength: 500,
    })
    fc.assert(
      fc.property(printableAscii, (input) => {
        expect(sanitizeForLog(input)).toBe(input)
      }),
    )
  })

  it('replaces all control characters with spaces', () => {
    // Generate strings with control characters (0x00-0x1F, 0x7F)
    const controlChar = fc.oneof(
      fc.integer({ min: 0x00, max: 0x1f }),
      fc.constant(0x7f),
    ).map((code) => String.fromCharCode(code))
    const controlString = fc.string({ unit: controlChar, minLength: 1, maxLength: 100 })
    fc.assert(
      fc.property(controlString, (input) => {
        const result = sanitizeForLog(input)
        // Every character should be a space (control chars replaced)
        for (const ch of result) {
          expect(ch).toBe(' ')
        }
      }),
    )
  })
})
