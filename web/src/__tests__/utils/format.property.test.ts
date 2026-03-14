import { describe, it, expect } from 'vitest'
import fc from 'fast-check'
import {
  formatDate,
  formatUptime,
  formatCurrency,
  formatLabel,
  formatNumber,
} from '@/utils/format'

describe('formatDate (property-based)', () => {
  it('always returns a string for any string input', () => {
    fc.assert(
      fc.property(fc.string(), (input) => {
        expect(typeof formatDate(input)).toBe('string')
      }),
    )
  })

  it('never throws on any string input', () => {
    fc.assert(
      fc.property(fc.string(), (input) => {
        expect(() => formatDate(input)).not.toThrow()
      }),
    )
  })

  it('returns em dash for empty strings', () => {
    expect(formatDate('')).toBe('\u2014')
  })

  it('returns em dash for null and undefined', () => {
    expect(formatDate(null)).toBe('\u2014')
    expect(formatDate(undefined)).toBe('\u2014')
  })
})

describe('formatUptime (property-based)', () => {
  it('always returns a non-empty string for any number', () => {
    fc.assert(
      fc.property(fc.double(), (input) => {
        const result = formatUptime(input)
        expect(typeof result).toBe('string')
        expect(result.length).toBeGreaterThan(0)
      }),
    )
  })

  it('never throws on any number input', () => {
    fc.assert(
      fc.property(fc.double(), (input) => {
        expect(() => formatUptime(input)).not.toThrow()
      }),
    )
  })

  it('treats negative numbers as 0 (returns "0m")', () => {
    fc.assert(
      fc.property(
        fc.double({ max: -0.001, noNaN: true }),
        (input) => {
          expect(formatUptime(input)).toBe('0m')
        },
      ),
    )
  })

  it('treats NaN and Infinity as 0 (returns "0m")', () => {
    expect(formatUptime(NaN)).toBe('0m')
    expect(formatUptime(Infinity)).toBe('0m')
    expect(formatUptime(-Infinity)).toBe('0m')
  })

  it('contains only valid time components (d, h, m)', () => {
    fc.assert(
      fc.property(
        // Keep values in a range that avoids scientific notation in Math.floor output
        fc.double({ min: 0, max: 1e15, noNaN: true }),
        (input) => {
          const result = formatUptime(input)
          // Result should match pattern like "1d 2h 3m" or "0m"
          expect(result).toMatch(/^(\d+d\s?)?(\d+h\s?)?(\d+m)?$/)
        },
      ),
    )
  })
})

describe('formatCurrency (property-based)', () => {
  it('result contains "$" for any finite number', () => {
    fc.assert(
      fc.property(
        fc.double({ noNaN: true, noDefaultInfinity: true }),
        (input) => {
          const result = formatCurrency(input)
          expect(result).toContain('$')
        },
      ),
    )
  })

  it('never throws on any finite number', () => {
    fc.assert(
      fc.property(
        fc.double({ noNaN: true, noDefaultInfinity: true }),
        (input) => {
          expect(() => formatCurrency(input)).not.toThrow()
        },
      ),
    )
  })

  it('always returns a string', () => {
    fc.assert(
      fc.property(
        fc.double({ noNaN: true, noDefaultInfinity: true }),
        (input) => {
          expect(typeof formatCurrency(input)).toBe('string')
        },
      ),
    )
  })
})

describe('formatLabel (property-based)', () => {
  it('each underscore-separated segment has its first char uppercased', () => {
    // Use snake_case-like strings (word chars separated by underscores) to avoid
    // edge cases with spaces in the input confounding the split logic.
    const snakeCaseStr = fc.stringMatching(/^[a-z][a-z0-9]*(_[a-z][a-z0-9]*)*$/)
    fc.assert(
      fc.property(snakeCaseStr, (input) => {
        const result = formatLabel(input)
        const inputParts = input.split('_')
        const resultParts = result.split(' ')
        expect(resultParts.length).toBe(inputParts.length)
        for (let i = 0; i < inputParts.length; i++) {
          const firstChar = resultParts[i].charAt(0)
          expect(firstChar).toBe(inputParts[i].charAt(0).toUpperCase())
        }
      }),
    )
  })

  it('never throws on any string', () => {
    fc.assert(
      fc.property(fc.string(), (input) => {
        expect(() => formatLabel(input)).not.toThrow()
      }),
    )
  })

  it('always returns a string', () => {
    fc.assert(
      fc.property(fc.string(), (input) => {
        expect(typeof formatLabel(input)).toBe('string')
      }),
    )
  })

  it('replaces underscores with spaces', () => {
    // Generate arrays of non-underscore strings, join with '_'
    const nonUnderscoreStr = fc.string({ minLength: 1 }).filter((s) => !s.includes('_'))
    fc.assert(
      fc.property(
        fc.array(nonUnderscoreStr, { minLength: 1, maxLength: 5 }),
        (parts) => {
          const input = parts.join('_')
          const result = formatLabel(input)
          expect(result).not.toContain('_')
        },
      ),
    )
  })
})

describe('formatNumber (property-based)', () => {
  it('always returns a string for any finite number', () => {
    fc.assert(
      fc.property(
        fc.double({ noNaN: true, noDefaultInfinity: true }),
        (input) => {
          expect(typeof formatNumber(input)).toBe('string')
        },
      ),
    )
  })

  it('never throws on any finite number', () => {
    fc.assert(
      fc.property(
        fc.double({ noNaN: true, noDefaultInfinity: true }),
        (input) => {
          expect(() => formatNumber(input)).not.toThrow()
        },
      ),
    )
  })

  it('returns a non-empty string', () => {
    fc.assert(
      fc.property(
        fc.double({ noNaN: true, noDefaultInfinity: true }),
        (input) => {
          expect(formatNumber(input).length).toBeGreaterThan(0)
        },
      ),
    )
  })
})
