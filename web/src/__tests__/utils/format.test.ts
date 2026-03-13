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
    // Use numeric month check to avoid locale sensitivity
    expect(result).toMatch(/12|Mar/)
  })

  it('returns dash for invalid date string', () => {
    expect(formatDate('not-a-date')).toBe('—')
  })
})

describe('formatRelativeTime', () => {
  it('returns dash for null', () => {
    expect(formatRelativeTime(null)).toBe('—')
  })

  it('returns "just now" for recent timestamps', () => {
    const recent = new Date(Date.now() - 5_000).toISOString()
    expect(formatRelativeTime(recent)).toBe('just now')
  })

  it('returns formatted date for future timestamps', () => {
    const future = new Date(Date.now() + 60_000).toISOString()
    // Future dates fall through to formatDate instead of 'just now'
    const result = formatRelativeTime(future)
    expect(result).not.toBe('just now')
    expect(result).not.toBe('—')
  })

  it('returns dash for invalid date string', () => {
    expect(formatRelativeTime('not-a-date')).toBe('—')
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

  it('formats negative value', () => {
    const result = formatCurrency(-45.67)
    expect(result).toContain('$')
    expect(result).toContain('45')
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

  it('formats round hours without trailing 0m', () => {
    expect(formatUptime(3600)).toBe('1h')
  })

  it('formats days hours and minutes', () => {
    expect(formatUptime(90060)).toBe('1d 1h 1m')
  })

  it('formats zero seconds as 0m', () => {
    expect(formatUptime(0)).toBe('0m')
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
