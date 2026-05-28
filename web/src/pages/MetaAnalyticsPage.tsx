/**
 * Meta-analytics page.
 *
 * Cross-cutting view of the meta layer: live signals (anomalies the meta loop
 * has surfaced), the active proposals queue, and the configured A/B tests.
 * The underlying ``getSignals`` / ``listProposals`` endpoints already exist;
 * this page wraps them in a single cohesive read-only surface.
 */
import { Loader2, Sparkles } from 'lucide-react'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { MetricCard } from '@/components/ui/metric-card'
import { SectionCard } from '@/components/ui/section-card'
import type { ProposalSummary, SignalsResponse } from '@/api/endpoints/meta'
import { formatNumber } from '@/utils/format'

import { useMetaAnalyticsData } from './meta/useMetaAnalyticsData'

interface MetaAnalyticsDisplayState {
  bothFailed: boolean
  showLoading: boolean
  showEmptySignals: boolean
  showSignalsByDomain: boolean
  showActiveProposals: boolean
}

function deriveDisplayState(
  data: ReturnType<typeof useMetaAnalyticsData>,
): MetaAnalyticsDisplayState {
  const bothFailed = data.signalsError !== null && data.proposalsError !== null
  const showLoading = data.loading && !data.signals && data.proposals.length === 0
  const showEmptySignals =
    !data.loading &&
    data.signalsError === null &&
    data.signals != null &&
    data.signals.domains.length === 0
  return {
    bothFailed,
    showLoading,
    showEmptySignals,
    showSignalsByDomain: data.signals != null && data.signals.domains.length > 0,
    showActiveProposals: data.proposals.length > 0,
  }
}

export default function MetaAnalyticsPage() {
  const data = useMetaAnalyticsData()
  const display = deriveDisplayState(data)

  return (
    <div className="space-y-section-gap">
      <ListHeader title="Meta analytics" />
      <MetaAnalyticsErrorBanners
        bothFailed={display.bothFailed}
        signalsError={data.signalsError}
        proposalsError={data.proposalsError}
      />
      {display.showLoading ? <LoadingSpinner /> : <MetaAnalyticsBody data={data} display={display} />}
    </div>
  )
}

function LoadingSpinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <Loader2 className="size-6 animate-spin text-text-muted" />
    </div>
  )
}

interface MetaAnalyticsBodyProps {
  data: ReturnType<typeof useMetaAnalyticsData>
  display: MetaAnalyticsDisplayState
}

function MetaAnalyticsBody({ data, display }: MetaAnalyticsBodyProps) {
  return (
    <>
      {data.signals && (
        <SignalOverviewSection
          signals={data.signals}
          proposalCount={data.proposals.length}
        />
      )}
      {display.showSignalsByDomain && data.signals && (
        <SignalsByDomainSection signals={data.signals} />
      )}
      {display.showEmptySignals && (
        <EmptyState
          title="No meta signals yet"
          description="Signals appear here once the meta-analysis loop has run at least once."
        />
      )}
      {display.showActiveProposals && <ActiveProposalsSection proposals={data.proposals} />}
    </>
  )
}

interface MetaAnalyticsErrorBannersProps {
  bothFailed: boolean
  signalsError: string | null
  proposalsError: string | null
}

function MetaAnalyticsErrorBanners({
  bothFailed,
  signalsError,
  proposalsError,
}: MetaAnalyticsErrorBannersProps) {
  if (bothFailed) {
    return (
      <ErrorBanner
        severity="error"
        title="Could not load meta analytics"
        description={`Signals: ${signalsError}. Proposals: ${proposalsError}.`}
      />
    )
  }
  return (
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
  )
}

interface SignalOverviewSectionProps {
  signals: SignalsResponse
  proposalCount: number
}

function SignalOverviewSection({ signals, proposalCount }: SignalOverviewSectionProps) {
  const healthyDomains = signals.domains.filter(
    (d) => d.status === 'ok' || d.status === 'healthy',
  ).length
  return (
    <SectionCard title="Signal overview" icon={Sparkles}>
      <div className="grid grid-cols-1 gap-grid-gap md:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Meta enabled" value={signals.enabled ? 'Yes' : 'No'} />
        <MetricCard label="Domains tracked" value={formatNumber(signals.domains.length)} />
        <MetricCard label="Active proposals" value={formatNumber(proposalCount)} />
        <MetricCard label="Healthy domains" value={formatNumber(healthyDomains)} />
      </div>
    </SectionCard>
  )
}

interface SignalsByDomainSectionProps {
  signals: SignalsResponse
}

function SignalsByDomainSection({ signals }: SignalsByDomainSectionProps) {
  return (
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
  )
}

interface ActiveProposalsSectionProps {
  proposals: readonly ProposalSummary[]
}

function ActiveProposalsSection({ proposals }: ActiveProposalsSectionProps) {
  return (
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
  )
}
