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

  bench('formatRelativeTime x100', () => {
    for (let i = 0; i < 100; i++) {
      formatRelativeTime(PAST_ISO)
    }
  })
})
