/** Formatting utilities for dates, currency, and numbers. */

import { createLogger } from '@/lib/logger'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { getLocale } from '@/utils/locale'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('format')

const MS_PER_SECOND = 1000
const SEC_PER_MIN = 60
const SEC_PER_HOUR = 3600
const SEC_PER_DAY = 86400
const SEC_PER_WEEK = 604800
const BYTES_PER_KB = 1024
const COMPACT_K_THRESHOLD = 1000

type DateInput = string | number | Date | null | undefined

const DATE_ONLY_RE = /^(\d{4})-(\d{2})-(\d{2})$/

/**
 * Memoized locale-validation cache. ``Intl.getCanonicalLocales`` throws
 * ``RangeError`` on malformed BCP 47 tags; we catch that once per tag
 * and remember the outcome so formatter helpers can fall back to
 * ``getLocale()`` instead of crashing the render path.
 */
const LOCALE_VALIDITY_CACHE = new Map<string, boolean>()

function isValidLocale(locale: string): boolean {
  const cached = LOCALE_VALIDITY_CACHE.get(locale)
  if (cached !== undefined) return cached
  let valid: boolean
  try {
    Intl.getCanonicalLocales(locale)
    valid = true
  } catch {
    valid = false
  }
  LOCALE_VALIDITY_CACHE.set(locale, valid)
  return valid
}

/**
 * Return ``locale`` when it is a valid BCP 47 tag, otherwise fall back
 * to {@link getLocale}. Prevents caller-supplied malformed locales
 * (e.g. from a settings store that has not yet been validated) from
 * throwing ``RangeError`` inside ``toLocaleString`` / ``Intl.*``.
 */
function resolveLocale(locale: string): string {
  return isValidLocale(locale) ? locale : getLocale()
}

/**
 * Parse {@link DateInput} to a ``Date`` (or ``null`` on invalid input).
 *
 * Date-only ISO strings (``YYYY-MM-DD``) are parsed into the local
 * midnight of that calendar day rather than the UTC midnight
 * ``new Date(string)`` would produce -- the latter can shift the
 * displayed day backward for viewers in negative-UTC timezones.
 */
function toDate(value: DateInput): Date | null {
  if (value == null) return null
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value
  }
  if (typeof value === 'string') {
    const match = DATE_ONLY_RE.exec(value)
    if (match) {
      const [, y, m, d] = match
      const year = Number(y)
      const monthIdx = Number(m) - 1
      const day = Number(d)
      const local = new Date(year, monthIdx, day)
      // ``new Date(2025, 1, 30)`` silently wraps to 2025-03-02; reject
      // overflowed inputs (e.g. ``2025-02-30``) so only true calendar
      // days are accepted.
      if (
        Number.isNaN(local.getTime()) ||
        local.getFullYear() !== year ||
        local.getMonth() !== monthIdx ||
        local.getDate() !== day
      ) {
        return null
      }
      return local
    }
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

/**
 * Format a date as date + time (e.g. "Jan 15, 2025, 10:30 AM").
 *
 * Accepts an ISO string, a `Date`, or a millisecond timestamp.
 */
export function formatDateTime(
  value: DateInput,
  locale: string = getLocale(),
): string {
  const date = toDate(value)
  if (!date) return '--'
  return date.toLocaleString(resolveLocale(locale), {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Alias of {@link formatDateTime} kept for the existing call sites that
 * import `formatDate`. New code should prefer `formatDateTime` for
 * clarity or `formatDateOnly` when no time is needed.
 */
export const formatDate = formatDateTime

/**
 * Format a date as a date-only string (e.g. "Jan 15, 2025").
 *
 * Accepts an ISO string, a `Date`, or a millisecond timestamp.
 */
export function formatDateOnly(
  value: DateInput,
  locale: string = getLocale(),
): string {
  const date = toDate(value)
  if (!date) return '--'
  return date.toLocaleDateString(resolveLocale(locale), {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

/**
 * Format a date as a time-only string (e.g. "10:30 AM").
 *
 * Accepts an ISO string, a `Date`, or a millisecond timestamp.
 */
export function formatTime(
  value: DateInput,
  locale: string = getLocale(),
): string {
  const date = toDate(value)
  if (!date) return '--'
  return date.toLocaleTimeString(resolveLocale(locale), {
    hour: '2-digit',
    minute: '2-digit',
  })
}

/**
 * Format a short day label for chart axes (e.g. "Jan 15").
 *
 * Accepts either an ISO string, a `Date`, or a millisecond timestamp.
 */
export function formatDayLabel(
  value: string | number | Date,
  locale: string = getLocale(),
): string {
  const date = toDate(value)
  if (!date) return '--'
  return date.toLocaleDateString(resolveLocale(locale), {
    month: 'short',
    day: 'numeric',
  })
}

/**
 * Format today's short day label (e.g. "Jan 15"). Useful as a reference
 * line label on burn/trend charts.
 */
export function formatTodayLabel(locale: string = getLocale()): string {
  return formatDayLabel(new Date(), locale)
}

/**
 * Format a date as locale-aware relative time (e.g. "5 minutes ago",
 * "il y a 5 minutes", "hace 5 minutos"). Uses `Intl.RelativeTimeFormat`
 * with `numeric: 'auto'` so near boundaries render as "yesterday" /
 * "tomorrow" rather than "1 day ago" / "in 1 day".
 *
 * Falls back to {@link formatDateTime} for dates older than a week, for
 * future dates, and for invalid/missing input.
 */
export function formatRelativeTime(
  iso: string | null | undefined,
  locale: string = getLocale(),
): string {
  if (!iso) return '--'
  // Route through ``toDate`` so ``YYYY-MM-DD`` strings are read as
  // local midnight (same parsing contract as ``formatDateTime`` and
  // siblings). ``new Date(iso)`` would read them as UTC midnight and
  // shift the displayed day in negative-UTC timezones.
  const date = toDate(iso)
  if (!date) return '--'
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  if (diffMs < 0) return formatDateTime(iso, locale)
  const diffSec = Math.floor(diffMs / MS_PER_SECOND)
  if (diffSec >= SEC_PER_WEEK) return formatDateTime(iso, locale)

  const rtf = new Intl.RelativeTimeFormat(resolveLocale(locale), {
    numeric: 'auto',
  })
  if (diffSec < SEC_PER_MIN) return rtf.format(-diffSec, 'second')
  if (diffSec < SEC_PER_HOUR) {
    return rtf.format(-Math.floor(diffSec / SEC_PER_MIN), 'minute')
  }
  if (diffSec < SEC_PER_DAY) {
    return rtf.format(-Math.floor(diffSec / SEC_PER_HOUR), 'hour')
  }
  return rtf.format(-Math.floor(diffSec / SEC_PER_DAY), 'day')
}

/**
 * Format a non-negative number of seconds as a compact elapsed string:
 * `Xs`, `Xm Ys`, `Xh Ym`, or `Xd Yh`. Negative or non-finite inputs
 * render as `--` so a clock skew or missing value never produces
 * `NaNs`. The components are written in British English with single-
 * letter unit suffixes for monospace columns.
 */
export function formatElapsed(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '--'
  const total = Math.floor(seconds)
  if (total < SEC_PER_MIN) return `${total}s`
  if (total < SEC_PER_HOUR) {
    const minutes = Math.floor(total / SEC_PER_MIN)
    const remainingSeconds = total % SEC_PER_MIN
    return remainingSeconds === 0 ? `${minutes}m` : `${minutes}m ${remainingSeconds}s`
  }
  if (total < SEC_PER_DAY) {
    const hours = Math.floor(total / SEC_PER_HOUR)
    const remainingMinutes = Math.floor((total % SEC_PER_HOUR) / SEC_PER_MIN)
    return remainingMinutes === 0 ? `${hours}h` : `${hours}h ${remainingMinutes}m`
  }
  const days = Math.floor(total / SEC_PER_DAY)
  const remainingHours = Math.floor((total % SEC_PER_DAY) / SEC_PER_HOUR)
  return remainingHours === 0 ? `${days}d` : `${days}d ${remainingHours}h`
}

/** ISO 4217 currencies that use zero decimal places. */
const ZERO_DECIMAL_CURRENCIES = new Set(['BIF','CLP','DJF','GNF','HUF','ISK','JPY','KMF','KRW','MGA','PYG','RWF','UGX','VND','VUV','XAF','XOF','XPF'])

/** ISO 4217 currencies that use three decimal places. */
const THREE_DECIMAL_CURRENCIES = new Set(['BHD','IQD','JOD','KWD','LYD','OMR','TND'])

/**
 * Format a currency value using the given ISO 4217 currency code.
 * Defaults to {@link DEFAULT_CURRENCY} when no code is provided.
 */
export function formatCurrency(
  value: number,
  currencyCode: string = DEFAULT_CURRENCY,
  locale: string = getLocale(),
): string {
  if (!Number.isFinite(value)) return '--'
  try {
    return new Intl.NumberFormat(resolveLocale(locale), {
      style: 'currency',
      currency: currencyCode,
    }).format(value)
  } catch (error) {
    log.error(
      'Intl.NumberFormat failed for currency',
      sanitizeForLog({ currencyCode }),
      error,
    )
    const digits = ZERO_DECIMAL_CURRENCIES.has(currencyCode) ? 0 : THREE_DECIMAL_CURRENCIES.has(currencyCode) ? 3 : 2
    return `${currencyCode} ${value.toFixed(digits)}`
  }
}

/**
 * Format a currency value compactly for chart axes (e.g. "$5", "$10K").
 * Exact format depends on locale and currency. Falls back to "CODE N" on
 * error without silently swapping the currency.
 */
export function formatCurrencyCompact(
  value: number,
  currencyCode: string = DEFAULT_CURRENCY,
  locale: string = getLocale(),
): string {
  if (!Number.isFinite(value)) return '--'
  try {
    return new Intl.NumberFormat(resolveLocale(locale), {
      style: 'currency',
      currency: currencyCode,
      maximumFractionDigits: 0,
      notation: 'compact',
    }).format(value)
  } catch (error) {
    log.error(
      'Intl.NumberFormat compact failed for currency',
      sanitizeForLog({ currencyCode }),
      error,
    )
    return `${currencyCode} ${Math.round(value)}`
  }
}

/**
 * Format a number with locale-appropriate separators.
 */
export function formatNumber(
  value: number,
  locale: string = getLocale(),
): string {
  if (!Number.isFinite(value)) return '--'
  return new Intl.NumberFormat(resolveLocale(locale)).format(value)
}

/**
 * Format a count of tokens for display. Values under 1000 use
 * locale-appropriate separators (typically just the number); larger
 * values use compact notation (e.g. "12K", "1.5M").
 */
export function formatTokenCount(
  value: number,
  locale: string = getLocale(),
): string {
  if (!Number.isFinite(value)) return '--'
  if (value < COMPACT_K_THRESHOLD) return formatNumber(value, locale)
  return new Intl.NumberFormat(resolveLocale(locale), {
    notation: 'compact',
    maximumFractionDigits: 1,
  }).format(value)
}

/**
 * Format seconds as a human-readable uptime string.
 */
export function formatUptime(seconds: number): string {
  const s = (!Number.isFinite(seconds) || seconds < 0) ? 0 : seconds
  const days = Math.floor(s / SEC_PER_DAY)
  const hours = Math.floor((s % SEC_PER_DAY) / SEC_PER_HOUR)
  const mins = Math.floor((s % SEC_PER_HOUR) / SEC_PER_MIN)
  const parts: string[] = []
  if (days > 0) parts.push(`${days}d`)
  if (hours > 0) parts.push(`${hours}h`)
  if (mins > 0 || parts.length === 0) parts.push(`${mins}m`)
  return parts.join(' ')
}

/**
 * Format a byte count to a human-readable size string (e.g. "1.2 MB").
 */
export function formatFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return '--'
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.max(0, Math.min(Math.floor(Math.log(bytes) / Math.log(BYTES_PER_KB)), units.length - 1))
  const value = bytes / BYTES_PER_KB ** exponent
  return exponent === 0 ? `${bytes} B` : `${value.toFixed(1)} ${units[exponent]}`
}

/**
 * Capitalize and format a snake_case or kebab-case string for display.
 */
export function formatLabel(value: string): string {
  return value
    .split(/[_-]/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}
