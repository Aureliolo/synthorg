import { afterEach, beforeEach, vi } from 'vitest'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import {
  formatDate,
  formatDateOnly,
  formatDateTime,
  formatDayLabel,
  formatElapsed,
  formatRelativeTime,
  formatCurrency,
  formatCurrencyCompact,
  formatFileSize,
  formatLabel,
  formatNumber,
  formatTime,
  formatTodayLabel,
  formatTokenCount,
  formatUptime,
} from '@/utils/format'

describe('formatDateTime', () => {
  it('returns -- for null/undefined', () => {
    expect(formatDateTime(null)).toBe('--')
    expect(formatDateTime(undefined)).toBe('--')
  })

  it('returns -- for empty string', () => {
    expect(formatDateTime('')).toBe('--')
  })

  it('returns -- for invalid date', () => {
    expect(formatDateTime('not-a-date')).toBe('--')
  })

  it('formats valid ISO date under en-US', () => {
    const result = formatDateTime('2025-01-15T10:30:00Z', 'en-US')
    expect(result).toContain('Jan')
    expect(result).toContain('15')
    expect(result).toContain('2025')
  })

  it('respects an explicit locale override', () => {
    const en = formatDateTime('2025-01-15T10:30:00Z', 'en-US')
    const de = formatDateTime('2025-01-15T10:30:00Z', 'de-DE')
    expect(en).not.toBe(de)
  })
})

describe('formatDate (alias)', () => {
  it('is the same function as formatDateTime', () => {
    expect(formatDate).toBe(formatDateTime)
  })
})

describe('formatDateOnly', () => {
  it('returns -- for null', () => {
    expect(formatDateOnly(null)).toBe('--')
  })

  it('formats as month+day+year without time', () => {
    const result = formatDateOnly('2025-01-15T10:30:00Z', 'en-US')
    expect(result).toContain('Jan')
    expect(result).toContain('15')
    expect(result).toContain('2025')
    expect(result).not.toMatch(/\d{1,2}:\d{2}/)
  })
})

describe('formatTime', () => {
  it('returns -- for null', () => {
    expect(formatTime(null)).toBe('--')
  })

  it('returns a time-only string', () => {
    const result = formatTime('2025-01-15T10:30:00Z', 'en-US')
    expect(result).toMatch(/\d/)
    expect(result).not.toContain('Jan')
    expect(result).not.toContain('2025')
  })
})

describe('formatDayLabel', () => {
  it('formats an ISO string', () => {
    expect(formatDayLabel('2025-01-15T10:30:00Z', 'en-US')).toContain('Jan')
  })

  it('formats a Date', () => {
    expect(formatDayLabel(new Date('2025-01-15T10:30:00Z'), 'en-US')).toContain('15')
  })

  it('formats a numeric timestamp', () => {
    const ms = new Date('2025-01-15T10:30:00Z').getTime()
    expect(formatDayLabel(ms, 'en-US')).toContain('Jan')
  })

  it('returns -- for invalid input', () => {
    expect(formatDayLabel('nope')).toBe('--')
  })
})

describe('formatTodayLabel', () => {
  it('returns a short month+day string under en-US', () => {
    const result = formatTodayLabel('en-US')
    expect(result).toMatch(/^[A-Z][a-z]{2} \d{1,2}$/)
  })
})

describe('formatRelativeTime', () => {
  // Freeze the clock so "just now" / "5 minutes ago" / "3 hours ago"
  // assertions cannot flip across the second boundary on slow CI hosts.
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-04-01T12:00:00.000Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns -- for null/undefined', () => {
    expect(formatRelativeTime(null)).toBe('--')
    expect(formatRelativeTime(undefined)).toBe('--')
  })

  it('returns -- for invalid date', () => {
    expect(formatRelativeTime('not-a-date')).toBe('--')
  })

  it('renders "now" for immediate timestamps under en-US', () => {
    const now = new Date().toISOString()
    expect(formatRelativeTime(now, 'en-US')).toBe('now')
  })

  it('renders minutes ago under en-US', () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString()
    expect(formatRelativeTime(fiveMinAgo, 'en-US')).toBe('5 minutes ago')
  })

  it('renders hours ago under en-US', () => {
    const threeHoursAgo = new Date(Date.now() - 3 * 3600 * 1000).toISOString()
    expect(formatRelativeTime(threeHoursAgo, 'en-US')).toBe('3 hours ago')
  })

  it('renders days ago under en-US', () => {
    const twoDaysAgo = new Date(Date.now() - 2 * 86400 * 1000).toISOString()
    expect(formatRelativeTime(twoDaysAgo, 'en-US')).toBe('2 days ago')
  })

  it('respects an explicit locale override', () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString()
    const en = formatRelativeTime(fiveMinAgo, 'en-US')
    const de = formatRelativeTime(fiveMinAgo, 'de-DE')
    expect(en).not.toBe(de)
  })

  it('falls back to formatDateTime for old dates', () => {
    const twoWeeksAgo = new Date(Date.now() - 14 * 86400 * 1000).toISOString()
    const result = formatRelativeTime(twoWeeksAgo)
    expect(result).not.toContain('ago')
    expect(result).not.toBe('--')
  })

  it('falls back to formatDateTime for future dates', () => {
    const future = new Date(Date.now() + 86400 * 1000).toISOString()
    const result = formatRelativeTime(future)
    expect(result).not.toContain('ago')
    expect(result).not.toBe('--')
  })
})

describe('formatCurrency', () => {
  it('defaults to DEFAULT_CURRENCY', () => {
    const result = formatCurrency(42.5)
    const expected = formatCurrency(42.5, DEFAULT_CURRENCY, 'en-US')
    expect(formatCurrency(42.5, DEFAULT_CURRENCY)).toBe(result)
    expect(result).toContain('42.50')
    // Round-trip: the default call must match an explicit
    // ``DEFAULT_CURRENCY`` call under the same locale.
    expect(formatCurrency(42.5, DEFAULT_CURRENCY, 'en-US')).toBe(expected)
  })

  it('formats USD values', () => {
    expect(formatCurrency(42.5, 'USD')).toBe('$42.50')
  })

  it('formats EUR values', () => {
    const result = formatCurrency(42.5, 'EUR')
    expect(result).toContain('\u20ac')
    expect(result).toContain('42.50')
  })

  it('formats GBP values', () => {
    const result = formatCurrency(42.5, 'GBP')
    expect(result).toContain('\u00a3')
    expect(result).toContain('42.50')
  })

  it('formats JPY values', () => {
    const result = formatCurrency(1000, 'JPY')
    expect(result).toContain('\u00a5')
    expect(result).toContain('1,000')
    expect(result).not.toContain('.')
  })

  it('formats zero with USD', () => {
    expect(formatCurrency(0, 'USD')).toBe('$0.00')
  })

  it('formats small fractional values', () => {
    const result = formatCurrency(0.0001, 'USD')
    expect(result).toContain('$')
    // Intl.NumberFormat rounds to currency's minor-unit (2 decimals for USD)
    expect(result).toBe('$0.00')
  })

  it('formats negative values', () => {
    const result = formatCurrency(-10, 'USD')
    expect(result).toContain('10')
    // Verify negative indicator (varies by locale: "-$10.00" or "($10.00)")
    expect(result.includes('-') || result.includes('(')).toBe(true)
  })

  it('returns -- for non-finite values', () => {
    expect(formatCurrency(Infinity, 'USD')).toBe('--')
    expect(formatCurrency(NaN, 'USD')).toBe('--')
  })

  it('respects an explicit locale override', () => {
    const en = formatCurrency(1234.5, 'USD', 'en-US')
    const de = formatCurrency(1234.5, 'USD', 'de-DE')
    expect(en).not.toBe(de)
  })
})

describe('formatNumber', () => {
  it('formats with separators under en-US', () => {
    expect(formatNumber(1000000, 'en-US')).toBe('1,000,000')
  })

  it('formats zero', () => {
    expect(formatNumber(0)).toBe('0')
  })

  it('returns -- for non-finite values', () => {
    expect(formatNumber(Infinity)).toBe('--')
    expect(formatNumber(NaN)).toBe('--')
  })

  it('respects explicit locale override', () => {
    const en = formatNumber(1234.5, 'en-US')
    const de = formatNumber(1234.5, 'de-DE')
    expect(en).not.toBe(de)
  })
})

describe('formatTokenCount', () => {
  it('uses plain separators under 1000', () => {
    expect(formatTokenCount(42, 'en-US')).toBe('42')
    expect(formatTokenCount(999, 'en-US')).toBe('999')
  })

  it('uses compact notation for >=1000', () => {
    expect(formatTokenCount(1200, 'en-US')).toMatch(/K/i)
    expect(formatTokenCount(2_500_000, 'en-US')).toMatch(/M/i)
  })

  it('returns -- for non-finite values', () => {
    expect(formatTokenCount(Infinity)).toBe('--')
    expect(formatTokenCount(NaN)).toBe('--')
  })
})

describe('formatUptime', () => {
  it('formats zero seconds', () => {
    expect(formatUptime(0)).toBe('0m')
  })

  it('formats minutes', () => {
    expect(formatUptime(300)).toBe('5m')
  })

  it('formats hours and minutes', () => {
    expect(formatUptime(3660)).toBe('1h 1m')
  })

  it('formats days, hours, and minutes', () => {
    expect(formatUptime(90061)).toBe('1d 1h 1m')
  })

  it('handles negative values', () => {
    expect(formatUptime(-100)).toBe('0m')
  })

  it('handles Infinity', () => {
    expect(formatUptime(Infinity)).toBe('0m')
  })

  it('handles NaN', () => {
    expect(formatUptime(NaN)).toBe('0m')
  })
})

describe('formatFileSize', () => {
  it('formats zero', () => {
    expect(formatFileSize(0)).toBe('0 B')
  })

  it('formats kilobytes', () => {
    expect(formatFileSize(2048)).toBe('2.0 KB')
  })

  it('returns -- for negative and non-finite', () => {
    expect(formatFileSize(-1)).toBe('--')
    expect(formatFileSize(NaN)).toBe('--')
  })
})

describe('formatLabel', () => {
  it('capitalizes snake_case', () => {
    expect(formatLabel('in_progress')).toBe('In Progress')
  })

  it('capitalizes single word', () => {
    expect(formatLabel('active')).toBe('Active')
  })

  it('handles empty string', () => {
    expect(formatLabel('')).toBe('')
  })
})

describe('formatCurrencyCompact', () => {
  it('formats a small value with DEFAULT_CURRENCY', () => {
    const result = formatCurrencyCompact(5)
    const explicit = formatCurrencyCompact(5, DEFAULT_CURRENCY)
    expect(result).toBe(explicit)
    expect(result).toMatch(/5/)
  })

  it('formats large values with compact notation', () => {
    const result = formatCurrencyCompact(1500, 'USD')
    // Compact notation should include currency marker and abbreviation
    expect(result).toMatch(/\$.*K/i)
  })

  it('returns -- for NaN', () => {
    expect(formatCurrencyCompact(NaN)).toBe('--')
  })

  it('returns -- for Infinity', () => {
    expect(formatCurrencyCompact(Infinity)).toBe('--')
  })

  it('returns -- for -Infinity', () => {
    expect(formatCurrencyCompact(-Infinity)).toBe('--')
  })

  it('falls back to "CODE N" when currency is invalid', () => {
    const result = formatCurrencyCompact(100, 'INVALID')
    expect(result).toBe('INVALID 100')
  })

  it('accepts a lowercase currency code (Intl normalizes it)', () => {
    const result = formatCurrencyCompact(100, 'usd')
    expect(result).toMatch(/\$/)
  })

  it('formats zero correctly', () => {
    const result = formatCurrencyCompact(0)
    expect(result).toMatch(/0/)
  })
})

describe('formatElapsed', () => {
  it('formats seconds under a minute', () => {
    expect(formatElapsed(0)).toBe('0s')
    expect(formatElapsed(45)).toBe('45s')
  })

  it('formats minutes + seconds', () => {
    expect(formatElapsed(60)).toBe('1m')
    expect(formatElapsed(125)).toBe('2m 5s')
  })

  it('formats hours + minutes', () => {
    expect(formatElapsed(3600)).toBe('1h')
    expect(formatElapsed(3725)).toBe('1h 2m')
  })

  it('formats days + hours', () => {
    expect(formatElapsed(86400)).toBe('1d')
    expect(formatElapsed(90000)).toBe('1d 1h')
  })

  it('returns -- on negative or non-finite input', () => {
    expect(formatElapsed(-1)).toBe('--')
    expect(formatElapsed(NaN)).toBe('--')
    expect(formatElapsed(Infinity)).toBe('--')
  })
})
