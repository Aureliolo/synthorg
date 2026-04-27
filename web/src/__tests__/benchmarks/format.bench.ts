/**
 * CodSpeed micro-benchmarks for `@/utils/format`.
 *
 * These helpers run on every cell render in tables, metric cards, and
 * timeseries charts; the `Intl.*Format` instance cache hidden inside
 * each helper is the actual hot path.
 *
 * All time-based fixtures are derived from `Date.now()` at module
 * load so they exercise the intended branches regardless of when the
 * bench runs. Hardcoded calendar timestamps would silently drift past
 * the helper's relative-time thresholds and start hitting the
 * fallback branch instead of the happy path.
 */
import { bench, describe } from 'vitest'

import { DEFAULT_CURRENCY } from '@/utils/currencies'
import {
  formatCurrency,
  formatCurrencyCompact,
  formatDateTime,
  formatNumber,
  formatRelativeTime,
  formatTokenCount,
} from '@/utils/format'

const ONE_MINUTE_MS = 60_000
const ONE_DAY_MS = 24 * 60 * 60 * 1000
const SIX_WEEKS_MS = 42 * ONE_DAY_MS

const NOW_MS = Date.now()
const PAST_ISO = new Date(NOW_MS - ONE_DAY_MS).toISOString()
const OLD_DATE_ISO = new Date(NOW_MS - SIX_WEEKS_MS).toISOString()
const TIMESTAMPS = Array.from({ length: 100 }, (_, i) =>
  new Date(NOW_MS - i * ONE_MINUTE_MS).toISOString(),
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

  bench('formatCurrency x100', () => {
    for (const v of CURRENCY_VALUES) {
      formatCurrency(v, DEFAULT_CURRENCY)
    }
  })

  bench('formatCurrencyCompact x100', () => {
    for (const v of CURRENCY_VALUES) {
      formatCurrencyCompact(v, DEFAULT_CURRENCY)
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
  bench('formatRelativeTime x100 (old date >1 week fallback)', () => {
    for (let i = 0; i < 100; i++) {
      formatRelativeTime(OLD_DATE_ISO)
    }
  })
})
