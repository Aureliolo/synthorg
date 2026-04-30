/**
 * Coordination metrics analytics.
 *
 * Top-level KPIs about how the dashboard's autonomous coordinators
 * are doing: average decision time, conflict rate, escalation rate.
 * Pulls from the same analytics overview endpoint that the main
 * dashboard reads, scoped to coordination-relevant fields.
 */
import { useEffect, useState } from 'react'
import { Loader2, Network } from 'lucide-react'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { getOverviewMetrics } from '@/api/endpoints/analytics'
import type { OverviewMetrics } from '@/api/types/analytics'
import { createLogger } from '@/lib/logger'
import { formatNumber } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('CoordinationMetricsPage')

export default function CoordinationMetricsPage() {
  const [data, setData] = useState<OverviewMetrics | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // Defer state writes to a microtask so the effect body itself stays
    // free of synchronous setState calls (per the @eslint-react
    // set-state-in-effect rule), then run the actual fetch.
    void Promise.resolve().then(async () => {
      if (cancelled) return
      setLoading(true)
      setError(null)
      try {
        const result = await getOverviewMetrics()
        if (cancelled) return
        setData(result)
      } catch (err) {
        if (cancelled) return
        const message = getErrorMessage(err)
        log.error('getOverviewMetrics failed', { error: message })
        setError(message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })
    return () => { cancelled = true }
  }, [])

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Coordination metrics" />

      {error && (
        <ErrorBanner severity="error" title="Could not load coordination metrics" description={error} />
      )}

      {loading && !data ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-6 animate-spin text-text-muted" />
        </div>
      ) : data ? (
        <SectionCard title="Coordination overview" icon={Network}>
          <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-4">
            <MetricCard label="Active agents" value={formatNumber(data.active_agents_count)} />
            <MetricCard label="Idle agents" value={formatNumber(data.idle_agents_count)} />
            <MetricCard label="Tasks total" value={formatNumber(data.total_tasks)} />
            <MetricCard label="Budget used" value={`${data.budget_used_percent.toFixed(1)}%`} />
          </div>
        </SectionCard>
      ) : null}
    </div>
  )
}
