import { describe, it, expect } from 'vitest'
import {
  formatDate,
  formatRelativeTime,
  formatCurrency,
  formatNumber,
  formatUptime,
  formatLabel,
} from '@/utils/format'

describe('formatDate', () => {
  it('returns dash for null', () => {
    expect(formatDate(null)).toBe('—')
  })

  it('returns dash for undefined', () => {
    expect(formatDate(undefined)).toBe('—')
  })

  it('formats valid ISO date', () => {
    const result = formatDate('2026-03-12T10:30:00Z')
    expect(result).toContain('2026')
    expect(result).toContain('Mar')
  })
})

describe('formatRelativeTime', () => {
  it('returns dash for null', () => {
    expect(formatRelativeTime(null)).toBe('—')
  })

  it('returns "just now" for recent timestamps', () => {
    const now = new Date().toISOString()
    expect(formatRelativeTime(now)).toBe('just now')
  })
})

describe('formatCurrency', () => {
  it('formats zero', () => {
    expect(formatCurrency(0)).toBe('$0.00')
  })

  it('formats positive value', () => {
    const result = formatCurrency(123.4567)
    expect(result).toContain('$')
    expect(result).toContain('123')
  })
})

describe('formatNumber', () => {
  it('formats integer', () => {
    expect(formatNumber(1234)).toBe('1,234')
  })

  it('formats zero', () => {
    expect(formatNumber(0)).toBe('0')
  })
})

describe('formatUptime', () => {
  it('formats seconds to minutes', () => {
    expect(formatUptime(120)).toBe('2m')
  })

  it('formats hours and minutes', () => {
    expect(formatUptime(3720)).toBe('1h 2m')
  })

  it('formats days hours and minutes', () => {
    expect(formatUptime(90060)).toBe('1d 1h 1m')
  })
})

describe('formatLabel', () => {
  it('formats snake_case', () => {
    expect(formatLabel('in_progress')).toBe('In Progress')
  })

  it('formats single word', () => {
    expect(formatLabel('active')).toBe('Active')
  })
})
