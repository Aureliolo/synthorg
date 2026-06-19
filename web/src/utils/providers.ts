/** Pure utility functions for provider data transformations. */

import type {
  ProviderConfig,
  ProviderHealthStatus,
  ProviderHealthSummary,
} from '@/api/types/providers'
import type { SemanticColor } from '@/utils/agent-status'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency, formatTokenCount as formatTokenCountBase } from '@/utils/format'

// ── Types ─────────────────────────────────────────────────────

/** Provider config enriched with the provider name (record key). */
export interface ProviderWithName extends ProviderConfig {
  name: string
}

export type ProviderSortKey = 'name' | 'health' | 'model_count'

export interface ProviderFilters {
  search?: string | undefined
  health?: ProviderHealthStatus | undefined
}

// ── Normalization ─────────────────────────────────────────────

/** Convert API's Record<string, ProviderConfig> to an array with names. */
export function normalizeProviders(
  record: Record<string, ProviderConfig>,
): ProviderWithName[] {
  return Object.entries(record).map(([name, config]) => ({
    ...config,
    name,
  }))
}

// ── Filtering ─────────────────────────────────────────────────

/** Client-side filter providers by search query and health status. */
export function filterProviders(
  providers: readonly ProviderWithName[],
  healthMap: Record<string, ProviderHealthSummary>,
  filters: ProviderFilters,
): ProviderWithName[] {
  let result = [...providers]

  if (filters.search) {
    const q = filters.search.toLowerCase()
    result = result.filter(
      (p) =>
        p.name.toLowerCase().includes(q) ||
        (p.base_url?.toLowerCase().includes(q) ?? false) ||
        (p.litellm_provider?.toLowerCase().includes(q) ?? false),
    )
  }

  if (filters.health) {
    const target = filters.health
    result = result.filter((p) => {
      const h = healthMap[p.name]
      return h?.health_status === target
    })
  }

  return result
}

// ── Sorting ───────────────────────────────────────────────────

const HEALTH_ORDER: Record<ProviderHealthStatus, number> = {
  down: 0,
  degraded: 1,
  unknown: 2,
  up: 3,
}

/** Client-side sort providers. */
export function sortProviders(
  providers: readonly ProviderWithName[],
  healthMap: Record<string, ProviderHealthSummary>,
  sortBy: ProviderSortKey,
  direction: 'asc' | 'desc',
): ProviderWithName[] {
  const sorted = [...providers]
  const dir = direction === 'asc' ? 1 : -1

  sorted.sort((a, b) => {
    switch (sortBy) {
      case 'name':
        return dir * a.name.localeCompare(b.name)
      case 'model_count':
        return dir * (a.models.length - b.models.length)
      case 'health': {
        const ha = healthMap[a.name]?.health_status
        const hb = healthMap[b.name]?.health_status
        const unknownOrder = Object.keys(HEALTH_ORDER).length
        const oa = ha ? HEALTH_ORDER[ha] : unknownOrder
        const ob = hb ? HEALTH_ORDER[hb] : unknownOrder
        return dir * (oa - ob)
      }
    }
  })

  return sorted
}

// ── Health colors ─────────────────────────────────────────────

const HEALTH_COLOR_MAP: Record<ProviderHealthStatus, SemanticColor | 'muted'> = {
  up: 'success',
  degraded: 'warning',
  down: 'danger',
  unknown: 'muted',
}

/** Map provider health status to semantic color. */
export function getProviderHealthColor(
  status: ProviderHealthStatus,
): SemanticColor | 'muted' {
  return HEALTH_COLOR_MAP[status]
}

// ── Formatting ────────────────────────────────────────────────

/** Format latency in ms to a human-readable string. */
export function formatLatency(ms: number | null): string {
  if (ms === null) return '--'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** Format error rate percentage. */
export function formatErrorRate(rate: number): string {
  if (rate === 0) return '0%'
  if (rate < 0.1) return '<0.1%'
  return `${rate.toFixed(1)}%`
}

/** Format a token count with K/M suffixes (locale-aware). */
export function formatTokenCount(n: number, locale?: string): string {
  return formatTokenCountBase(n, locale)
}

/**
 * Format a cost value using the project currency (defaults to
 * {@link DEFAULT_CURRENCY}).
 */
export function formatCost(
  cost: number,
  currencyCode: string = DEFAULT_CURRENCY,
): string {
  if (cost === 0) return formatCurrency(0, currencyCode)
  if (cost > 0 && cost < 0.01) {
    const symbol = formatCurrency(0.01, currencyCode).replace(/[\d.,\s]+/, '').trim()
    return `<${symbol}0.01`
  }
  return formatCurrency(cost, currencyCode)
}
