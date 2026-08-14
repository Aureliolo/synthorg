/**
 * Capability sources panel (Settings → Providers). Shows every declared
 * evidence source with its licence, what it last managed to read, and how old
 * the measurements still grading models are.
 *
 * The state this panel exists to make visible is a source that has stopped
 * answering while its earlier rows keep grading. Nothing else distinguishes
 * that from a source working normally: the rungs look the same, the models
 * look graded, and the only tell is a date. So a failing source says so on
 * the row, and a banner says it above the list.
 */
import { memo } from 'react'
import { AlertTriangle, Database, Loader2, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { SkeletonText } from '@/components/ui/skeleton'
import { StatusPill, type StatusPillTone } from '@/components/ui/status-pill'
import { ToggleField } from '@/components/ui/toggle-field'
import { EmptyState } from '@/components/ui/empty-state'
import type { CapabilitySourceDTO } from '@/api/types/providers'
import {
  failingSources,
  useCapabilitySources,
  type CapabilitySourcesController,
} from './useCapabilitySources'

/** Days below which an age reads as "today" rather than a rounded zero. */
const SAME_DAY_THRESHOLD = 1

function ageLabel(days: number | null): string {
  if (days === null) return 'never'
  if (days < SAME_DAY_THRESHOLD) return 'today'
  const whole = Math.floor(days)
  return whole === 1 ? '1 day ago' : `${String(whole)} days ago`
}

function healthTone(source: CapabilitySourceDTO): StatusPillTone {
  if (!source.enabled) return 'text-secondary'
  if (source.is_healthy) return 'success'
  return source.has_stale_evidence ? 'warning' : 'danger'
}

function healthLabel(source: CapabilitySourceDTO): string {
  if (!source.enabled) return 'Off'
  if (source.is_healthy) return 'Answering'
  return source.has_stale_evidence ? 'Stale' : 'No evidence'
}

function SourceCoverage({ source }: { source: CapabilitySourceDTO }) {
  if (source.last_succeeded_at === null) {
    return <p className="text-xs text-muted-foreground">No successful read yet.</p>
  }
  return (
    <p className="text-xs text-muted-foreground">
      {`${String(source.scores_written)} measurements from ${String(source.rows_read)} rows`}
      {source.rows_skipped > 0 ? ` (${String(source.rows_skipped)} not used)` : ''}
      {` · measured ${ageLabel(source.evidence_age_days)}`}
    </p>
  )
}

function SourceRow({
  source,
  busy,
  onToggle,
  onRefresh,
}: {
  source: CapabilitySourceDTO
  busy: boolean
  onToggle: (label: string, enabled: boolean) => void
  onRefresh: (label: string) => void
}) {
  return (
    <li className="rounded-md border border-border bg-surface p-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{source.display_name}</span>
            <StatusPill tone={healthTone(source)}>{healthLabel(source)}</StatusPill>
            {source.is_custom_url ? (
              <StatusPill tone="accent">Custom URL</StatusPill>
            ) : null}
          </div>
          <SourceCoverage source={source} />
          <p className="mt-1 text-xs text-muted-foreground">{source.cadence_note}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ToggleField
            label="Enabled"
            checked={source.enabled}
            onChange={(enabled) => {
              onToggle(source.label, enabled)
            }}
            disabled={busy}
          />
          <Button
            variant="secondary"
            size="sm"
            disabled={busy}
            onClick={() => {
              onRefresh(source.label)
            }}
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="size-4" aria-hidden />
            )}
            Refresh
          </Button>
        </div>
      </div>
      {source.last_error === '' ? null : (
        <ErrorBanner
          variant="inline"
          severity={source.has_stale_evidence ? 'warning' : 'error'}
          title={
            source.has_stale_evidence
              ? 'Not answering; earlier measurements still grading'
              : 'Not answering, and it has no earlier measurements here'
          }
          description={source.last_error}
          className="mt-3"
        />
      )}
      <p className="mt-3 text-xs text-muted-foreground">{source.licence_note}</p>
      {source.attribution === '' ? null : (
        <p className="mt-1 text-xs text-muted-foreground">{source.attribution}</p>
      )}
    </li>
  )
}

function SourcesBanner({ sources }: { sources: readonly CapabilitySourceDTO[] }) {
  const failing = failingSources(sources)
  if (failing.length === 0) return null
  const names = failing.map((s) => s.display_name).join(', ')
  const anyHealthy = sources.some((s) => s.enabled && s.is_healthy)
  return (
    <ErrorBanner
      variant="section"
      severity={anyHealthy ? 'warning' : 'error'}
      title={
        anyHealthy
          ? 'One source is not answering'
          : 'No enabled source is answering'
      }
      description={
        anyHealthy
          ? `${names} is down, so grading is running on the sources that are left. Models it alone measured keep their last rung until it recovers.`
          : `${names} is down. Models with no usable measurement fall back to the size-and-price heuristic, which is what evidence exists to correct.`
      }
      className="mb-4"
    />
  )
}

function SourcesList({ controller }: { controller: CapabilitySourcesController }) {
  const { state, setEnabled, refreshOne } = controller
  if (state.sources.length === 0) {
    return (
      <EmptyState
        icon={Database}
        title="No capability sources are declared"
        description="Model rungs come from the size-and-price heuristic alone."
      />
    )
  }
  return (
    <>
      <SourcesBanner sources={state.sources} />
      <ul className="space-y-3">
        {state.sources.map((source) => (
          <SourceRow
            key={source.label}
            source={source}
            busy={state.busyLabels.has(source.label)}
            onToggle={setEnabled}
            onRefresh={refreshOne}
          />
        ))}
      </ul>
    </>
  )
}

function RefreshAllButton({ controller }: { controller: CapabilitySourcesController }) {
  const { state, refreshAll } = controller
  return (
    <Button
      variant="secondary"
      size="sm"
      disabled={state.refreshingAll || state.loading}
      onClick={refreshAll}
    >
      {state.refreshingAll ? (
        <Loader2 className="size-4 animate-spin" aria-hidden />
      ) : (
        <RefreshCw className="size-4" aria-hidden />
      )}
      Refresh all
    </Button>
  )
}

export const CapabilitySourcesSection = memo(function CapabilitySourcesSection() {
  const controller = useCapabilitySources()
  const { state, load } = controller
  return (
    <SectionCard
      title="Capability evidence"
      icon={AlertTriangle}
      action={<RefreshAllButton controller={controller} />}
    >
      {state.error === null ? null : (
        <ErrorBanner
          variant="section"
          title="Could not load the capability sources"
          description={state.error}
          onRetry={load}
          className="mb-4"
        />
      )}
      {state.loading ? <SkeletonText lines={4} /> : <SourcesList controller={controller} />}
    </SectionCard>
  )
})
