import { describe, it, expect } from 'vitest'
import { sanitizeForLog } from '@/utils/logging'

describe('sanitizeForLog', () => {
  it('converts string values', () => {
    expect(sanitizeForLog('hello')).toBe('hello')
  })

  it('extracts message from Error objects', () => {
    expect(sanitizeForLog(new Error('oops'))).toBe('oops')
  })

  it('strips control characters', () => {
    expect(sanitizeForLog('line1\nline2\ttab\x00null')).toBe('line1 line2 tab null')
  })

  it('truncates to maxLen', () => {
    const long = 'a'.repeat(1000)
    expect(sanitizeForLog(long, 50).length).toBe(50)
  })

  it('uses default maxLen of 500', () => {
    const long = 'b'.repeat(1000)
    expect(sanitizeForLog(long).length).toBe(500)
  })

  it('converts numbers to string', () => {
    expect(sanitizeForLog(42)).toBe('42')
  })

  it('converts null/undefined', () => {
    expect(sanitizeForLog(null)).toBe('null')
    expect(sanitizeForLog(undefined)).toBe('undefined')
  })

  it('preserves printable ASCII characters', () => {
    const printable = ' !"#$%&\'()*+,-./0123456789:;<=>?@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\\]^_`abcdefghijklmnopqrstuvwxyz{|}~'
    expect(sanitizeForLog(printable)).toBe(printable)
  })

  it('replaces DEL character (0x7F)', () => {
    expect(sanitizeForLog('a\x7fb')).toBe('a b')
  })
})
