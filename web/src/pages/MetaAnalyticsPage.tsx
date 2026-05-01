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
import { sanitizeForLog } from '@/utils/logging'
import { formatNumber } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'

const log = createLogger('MetaAnalyticsPage')

export default function MetaAnalyticsPage() {
  const [signals, setSignals] = useState<SignalsResponse | null>(null)
  const [proposals, setProposals] = useState<readonly ProposalSummary[]>([])
  const [loading, setLoading] = useState(true)
  // Per-resource error state so the operator sees which fetch failed
  // (not a conflated "x; y" string). When both are non-null the page
  // is fully unavailable; when one is null the page renders the
  // available data plus a partial-failure banner pointing at the
  // failed resource.
  const [signalsError, setSignalsError] = useState<string | null>(null)
  const [proposalsError, setProposalsError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    // Defer setState writes to a microtask (per @eslint-react
    // set-state-in-effect) before kicking off the parallel fetches.
    void Promise.resolve().then(async () => {
      if (cancelled) return
      setLoading(true)
      setSignalsError(null)
      setProposalsError(null)
      const [signalsRes, proposalsRes] = await Promise.allSettled([getSignals(), listProposals()])
      if (cancelled) return
      if (signalsRes.status === 'fulfilled') {
        setSignals(signalsRes.value)
      } else {
        const message = getErrorMessage(signalsRes.reason)
        // SEC-1: sanitize before structured logging; UI keeps the raw
        // message because the user-facing ErrorBanner is human-authored.
        log.error('getSignals failed', { error: sanitizeForLog(message) })
        setSignalsError(message)
      }
      if (proposalsRes.status === 'fulfilled') {
        setProposals(proposalsRes.value)
      } else {
        const message = getErrorMessage(proposalsRes.reason)
        log.error('listProposals failed', { error: sanitizeForLog(message) })
        setProposalsError(message)
      }
      setLoading(false)
    })
    return () => { cancelled = true }
  }, [])

  const bothFailed = signalsError !== null && proposalsError !== null

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Meta analytics" />

      {bothFailed ? (
        <ErrorBanner
          severity="error"
          title="Could not load meta analytics"
          description={`Signals: ${signalsError}. Proposals: ${proposalsError}.`}
        />
      ) : (
        <>
          {signalsError && (
            <ErrorBanner
              severity="warning"
              title="Could not load meta signals"
              description={signalsError}
            />
          )}
          {proposalsError && (
            <ErrorBanner
              severity="warning"
              title="Could not load proposals"
              description={proposalsError}
            />
          )}
        </>
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
            // Suppress the empty state when the signals fetch failed
            // (signalsError != null). The ErrorBanner above already
            // tells the operator what happened; rendering "No meta
            // signals yet" alongside "Could not load meta signals"
            // would be misleading.
            !loading && signalsError === null && (
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
