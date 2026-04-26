/**
 * CodSpeed micro-benchmarks for `@/utils/format`.
 *
 * These helpers run on every cell render in tables, metric cards, and
 * timeseries charts; the `Intl.*Format` instance cache hidden inside
 * each helper is the actual hot path. Bench inputs cover both the
 * cache-warm and cache-cold paths.
 */
import { bench, describe } from 'vitest'

import {
  formatCurrency,
  formatCurrencyCompact,
  formatDateTime,
  formatNumber,
  formatRelativeTime,
  formatTokenCount,
} from '@/utils/format'

const NOW = new Date('2026-04-26T14:30:00Z')
const PAST_ISO = '2026-04-25T14:30:00Z'
const TIMESTAMPS = Array.from({ length: 100 }, (_, i) =>
  new Date(NOW.getTime() - i * 60_000).toISOString(),
)
const CURRENCY_VALUES = Array.from({ length: 100 }, (_, i) => 12.34 + i * 0.5)
const NUMBER_VALUES = Array.from({ length: 100 }, (_, i) => 1234 + i * 7)
const TOKEN_VALUES = Array.from({ length: 100 }, (_, i) => 1500 + i * 250)

describe('format helpers', () => {
  bench('formatDateTime x100', () => {
    for (const ts of TIMESTAMPS) {
      formatDateTime(ts)
    }
  })

  bench('formatCurrency x100 (USD)', () => {
    for (const v of CURRENCY_VALUES) {
      formatCurrency(v, 'USD')
    }
  })

  bench('formatCurrencyCompact x100 (USD)', () => {
    for (const v of CURRENCY_VALUES) {
      formatCurrencyCompact(v, 'USD')
    }
  })

  bench('formatNumber x100', () => {
    for (const v of NUMBER_VALUES) {
      formatNumber(v)
    }
  })

  bench('formatTokenCount x100', () => {
    for (const v of TOKEN_VALUES) {
      formatTokenCount(v)
    }
  })

  bench('formatRelativeTime x100 (1-day-old happy path)', () => {
    for (let i = 0; i < 100; i++) {
      formatRelativeTime(PAST_ISO)
    }
  })

  // Real WS payloads (sanitizeApproval / sanitizeMeeting / sanitizeTask)
  // can produce null/undefined timestamps; the helper short-circuits to
  // ``'--'`` early. Bench measures the null-check branch cost.
  bench('formatRelativeTime x100 (null/undefined fast path)', () => {
    for (let i = 0; i < 100; i++) {
      formatRelativeTime(null)
      formatRelativeTime(undefined)
    }
  })

  // Old timestamps (>1 week) fall through to the formatDateTime branch,
  // which exercises a different ``Intl.DateTimeFormat`` instance than
  // the relative-time formatter. Catches regressions in the fallback path.
  const OLD_DATE_ISO = '2026-03-15T14:30:00Z' // ~6 weeks before NOW
  bench('formatRelativeTime x100 (old date >1 week fallback)', () => {
    for (let i = 0; i < 100; i++) {
      formatRelativeTime(OLD_DATE_ISO)
    }
  })
})
