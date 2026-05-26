import {
  hasKey,
  isArrayOf,
  isBoolean,
  isFiniteNumber,
  isNumber,
  isObject,
  isOptionalString,
  isString,
  parseOrNull,
} from '@/utils/type-guards'

describe('type-guards', () => {
  describe('isObject', () => {
    it('accepts a non-null record', () => {
      expect(isObject({ a: 1 })).toBe(true)
      expect(isObject({})).toBe(true)
    })

    it('rejects null even though typeof null === object', () => {
      expect(isObject(null)).toBe(false)
    })

    it('rejects arrays', () => {
      expect(isObject([])).toBe(false)
      expect(isObject([1, 2])).toBe(false)
    })

    it('rejects primitives', () => {
      expect(isObject('a')).toBe(false)
      expect(isObject(1)).toBe(false)
      expect(isObject(true)).toBe(false)
      expect(isObject(undefined)).toBe(false)
    })
  })

  describe('hasKey', () => {
    it('accepts an object with the key', () => {
      expect(hasKey({ a: 1 }, 'a')).toBe(true)
    })

    it('rejects when the key is missing', () => {
      expect(hasKey({ a: 1 }, 'b')).toBe(false)
    })

    it('rejects on non-object input', () => {
      expect(hasKey(null, 'a')).toBe(false)
      expect(hasKey([], 'a')).toBe(false)
      expect(hasKey('x', 'a')).toBe(false)
    })
  })

  describe('isArrayOf', () => {
    it('accepts an array whose every element passes the guard', () => {
      expect(isArrayOf(['a', 'b'], isString)).toBe(true)
    })

    it('accepts an empty array', () => {
      expect(isArrayOf([], isString)).toBe(true)
    })

    it('rejects when one element fails', () => {
      expect(isArrayOf(['a', 1], isString)).toBe(false)
    })

    it('rejects non-arrays', () => {
      expect(isArrayOf('a', isString)).toBe(false)
      expect(isArrayOf(null, isString)).toBe(false)
    })
  })

  describe('isString / isOptionalString', () => {
    it('isString accepts strings only', () => {
      expect(isString('a')).toBe(true)
      expect(isString('')).toBe(true)
      expect(isString(1)).toBe(false)
      expect(isString(undefined)).toBe(false)
    })

    it('isOptionalString accepts strings and undefined', () => {
      expect(isOptionalString('a')).toBe(true)
      expect(isOptionalString(undefined)).toBe(true)
      expect(isOptionalString(null)).toBe(false)
      expect(isOptionalString(1)).toBe(false)
    })
  })

  describe('isNumber / isFiniteNumber', () => {
    it('isNumber accepts NaN and Infinity', () => {
      expect(isNumber(1)).toBe(true)
      expect(isNumber(0)).toBe(true)
      expect(isNumber(-1.5)).toBe(true)
      expect(isNumber(Number.NaN)).toBe(true)
      expect(isNumber(Number.POSITIVE_INFINITY)).toBe(true)
      expect(isNumber('1')).toBe(false)
    })

    it('isFiniteNumber rejects NaN and Infinity', () => {
      expect(isFiniteNumber(1)).toBe(true)
      expect(isFiniteNumber(0)).toBe(true)
      expect(isFiniteNumber(-1.5)).toBe(true)
      expect(isFiniteNumber(Number.NaN)).toBe(false)
      expect(isFiniteNumber(Number.POSITIVE_INFINITY)).toBe(false)
      expect(isFiniteNumber(Number.NEGATIVE_INFINITY)).toBe(false)
    })
  })

  describe('isBoolean', () => {
    it('accepts true / false', () => {
      expect(isBoolean(true)).toBe(true)
      expect(isBoolean(false)).toBe(true)
    })

    it('rejects truthy/falsy non-booleans', () => {
      expect(isBoolean(1)).toBe(false)
      expect(isBoolean(0)).toBe(false)
      expect(isBoolean('true')).toBe(false)
      expect(isBoolean(null)).toBe(false)
      expect(isBoolean(undefined)).toBe(false)
    })
  })

  describe('parseOrNull', () => {
    it('returns the narrowed value when guard passes', () => {
      const result = parseOrNull('hello', isString)
      // TS narrows to string here.
      expect(result).toBe('hello')
    })

    it('returns null when guard fails', () => {
      expect(parseOrNull(123, isString)).toBeNull()
      expect(parseOrNull(undefined, isString)).toBeNull()
    })

    it('composes with isArrayOf', () => {
      const result = parseOrNull(['a', 'b'], (v): v is string[] => isArrayOf(v, isString))
      expect(result).toEqual(['a', 'b'])
      expect(parseOrNull(['a', 1], (v): v is string[] => isArrayOf(v, isString))).toBeNull()
    })
  })
})
