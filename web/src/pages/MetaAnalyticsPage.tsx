/**
 * Meta-analytics page.
 *
 * Cross-cutting view of the meta layer: live signals (anomalies the
 * meta loop has surfaced), the active proposals queue, and the
 * configured A/B tests. The underlying ``getSignals`` /
 * ``listProposals`` endpoints already exist; this page wraps them
 * in a single cohesive read-only surface.
 */
import { useEffect, useState } from 'react'
import { Loader2, Sparkles } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import { getSignals, listProposals } from '@/api/endpoints/meta'
import type { ProposalSummary, SignalsResponse } from '@/api/endpoints/meta'
import { createLogger } from '@/lib/logger'
import { formatNumber } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('MetaAnalyticsPage')

export default function MetaAnalyticsPage() {
  const [signals, setSignals] = useState<SignalsResponse | null>(null)
  const [proposals, setProposals] = useState<readonly ProposalSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    void Promise.allSettled([getSignals(), listProposals()]).then(([signalsRes, proposalsRes]) => {
      if (cancelled) return
      const errors: string[] = []
      if (signalsRes.status === 'fulfilled') {
        setSignals(signalsRes.value)
      } else {
        const message = getErrorMessage(signalsRes.reason)
        log.error('getSignals failed', { error: message })
        errors.push(message)
      }
      if (proposalsRes.status === 'fulfilled') {
        setProposals(proposalsRes.value)
      } else {
        const message = getErrorMessage(proposalsRes.reason)
        log.error('listProposals failed', { error: message })
        errors.push(message)
      }
      if (errors.length > 0) setError(errors.join('; '))
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Meta analytics" />

      {error && (
        <ErrorBanner severity="error" title="Could not load meta analytics" description={error} />
      )}

      {loading && !signals && proposals.length === 0 ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-6 animate-spin text-text-muted" />
        </div>
      ) : (
        <>
          {signals && (
            <SectionCard title="Signal overview" icon={Sparkles}>
              <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-4">
                <MetricCard label="Meta enabled" value={signals.enabled ? 'Yes' : 'No'} />
                <MetricCard label="Domains tracked" value={formatNumber(signals.domains.length)} />
                <MetricCard label="Active proposals" value={formatNumber(proposals.length)} />
                <MetricCard
                  label="Healthy domains"
                  value={formatNumber(
                    signals.domains.filter((d) => d.status === 'ok' || d.status === 'healthy').length,
                  )}
                />
              </div>
            </SectionCard>
          )}

          {signals && signals.domains.length > 0 ? (
            <SectionCard title="Signals by domain">
              <ul className="divide-y divide-border">
                {signals.domains.map((domain) => (
                  <li key={domain.name} className="flex items-center gap-4 py-2 text-sm">
                    <span className="flex-1 font-medium text-foreground">{domain.name}</span>
                    <span className="rounded-md border border-border bg-card px-2 py-0.5 text-xs uppercase text-text-secondary">
                      {domain.status}
                    </span>
                  </li>
                ))}
              </ul>
            </SectionCard>
          ) : (
            !loading && (
              <EmptyState
                title="No meta signals yet"
                description="Signals appear here once the meta-analysis loop has run at least once."
              />
            )
          )}

          {proposals.length > 0 && (
            <SectionCard title="Active proposals">
              <ul className="divide-y divide-border">
                {proposals.map((proposal) => (
                  <li key={proposal.id} className="flex items-center gap-4 py-2 text-sm">
                    <span className="flex-1 truncate text-foreground">{proposal.title}</span>
                    <span className="rounded-md border border-border bg-card px-2 py-0.5 text-xs uppercase text-text-secondary">
                      {proposal.status}
                    </span>
                    <span className="text-xs text-text-muted">{proposal.risk_level}</span>
                  </li>
                ))}
              </ul>
            </SectionCard>
          )}
        </>
      )}
    </div>
  )
}
