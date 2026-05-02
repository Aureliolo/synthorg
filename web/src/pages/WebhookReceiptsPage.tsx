/**
 * Webhook receipt inspector.
 *
 * Per-connection list of recent inbound webhook deliveries. The
 * operator picks a connection from the existing connections store
 * and the page surfaces every received event with its status,
 * payload size, and any backend-captured error.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { ProgressIndicator } from '@/components/ui/progress-indicator'
import { SectionCard } from '@/components/ui/section-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SelectField } from '@/components/ui/select-field'
import { StatusBadge } from '@/components/ui/status-badge'
import type { AgentRuntimeStatus } from '@/lib/utils'
import { useConnectionsData } from '@/hooks/useConnectionsData'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { ROUTES } from '@/router/routes'
import { formatDateTime } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'
import {
  listWebhookActivity,
  type WebhookReceipt,
} from '@/api/endpoints/webhooks'

const log = createLogger('WebhookReceiptsPage')

// Backend ``WebhookReceipt.status`` is a free-form string; map known
// values onto the four-tone AgentRuntimeStatus the StatusBadge
// understands. Anything unrecognised falls back to ``idle`` so the
// dot still renders without a misleading colour.
function mapWebhookStatus(status: string): AgentRuntimeStatus {
  const lower = status.toLowerCase()
  if (lower === 'delivered' || lower === 'processed' || lower === 'success') {
    return 'active'
  }
  if (lower === 'failed' || lower === 'error') return 'error'
  if (lower === 'rejected' || lower === 'cancelled') return 'offline'
  return 'idle'
}

export default function WebhookReceiptsPage() {
  const { connections } = useConnectionsData()
  const [searchParams] = useSearchParams()
  // Pre-select via URL ?connection=... so the cross-link from
  // ConnectionsPage's row action lands directly on that connection's
  // receipts.
  const initialFromUrl = searchParams.get('connection') ?? ''
  const [selected, setSelected] = useState<string>(initialFromUrl)
  const [entries, setEntries] = useState<readonly WebhookReceipt[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Default the selector to the first connection that supports webhooks
  // (we don't have that flag locally; just pick the first connection),
  // AND reconcile when the connections list changes so a previously-
  // selected connection that has since been removed (renamed,
  // deleted, retracted by the backend) doesn't leave the dropdown
  // showing a stale name. The setState is deferred to a microtask so
  // the effect body itself stays free of synchronous setState calls
  // per the ESLint set-state-in-effect rule.
  useEffect(() => {
    const exists = selected !== '' && connections.some((c) => c.name === selected)
    if (exists) return
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled) return
      setSelected(connections[0]?.name ?? '')
    })
    return () => { cancelled = true }
  }, [connections, selected])

  // Per-request sequence number, bumped on every reload. The earlier
  // ``requestedFor !== selected`` check compared two values from the
  // same closure snapshot, so it never filtered an older response
  // after the user switched connections. The ref-backed counter
  // gives the in-flight request a stable identity that the latest
  // issued id can be compared against; a mismatch unambiguously
  // marks the response as stale.
  const requestSeqRef = useRef(0)
  // Latest connection name, mirrored into a ref so the in-flight
  // reload can compare against the most recent dropdown value at
  // settle time. The sequence guard alone leaves a window between
  // selection change and the next ``reload`` call where a still-
  // pending request could pass the sequence check; the selection-
  // ref check closes that window.
  const latestSelectedRef = useRef<string>(selected)
  latestSelectedRef.current = selected

  // If the operator changes the selection mid-flight, the older
  // request's response is dropped: the captured ``requestId`` no
  // longer matches ``requestSeqRef.current`` AND/OR the captured
  // ``requestedFor`` no longer matches the latest selection.
  // Without this guard, switching from connection A to B during a
  // slow A response would render A's receipts under B's label.
  const reload = useCallback(async () => {
    if (!selected) return
    const requestedFor = selected
    requestSeqRef.current += 1
    const requestId = requestSeqRef.current
    setLoading(true)
    setError(null)
    function isStale(): boolean {
      return (
        requestId !== requestSeqRef.current
        || latestSelectedRef.current !== requestedFor
      )
    }
    try {
      const rows = await listWebhookActivity(requestedFor)
      if (isStale()) return
      setEntries(rows)
    } catch (err) {
      if (isStale()) return
      const message = getErrorMessage(err)
      // SEC-1: connectionName is operator-controlled (URL / dropdown
      // value); sanitize before structured logging.
      log.error('listWebhookActivity failed', {
        connectionName: sanitizeForLog(requestedFor),
        error: sanitizeForLog(message),
      })
      setError(message)
    } finally {
      if (!isStale()) setLoading(false)
    }
  }, [selected])

  // Clear the previous connection's rows immediately on selection
  // change so the operator sees the loading state for the new
  // connection, not the stale rows from the previous one. The
  // setState is deferred to a microtask so the effect body itself
  // stays free of synchronous setState calls per the ESLint
  // set-state-in-effect rule. The request-sequence guard prevents
  // the late response from overwriting; this keeps the UI honest
  // in the meantime.
  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled) return
      setEntries([])
    })
    return () => { cancelled = true }
  }, [selected])

  useEffect(() => {
    void reload()
  }, [reload])

  // Memoise so a parent re-render that produces a new ``connections``
  // identity (without value-level changes) does not force the
  // SelectField underneath to re-render against a fresh array.
  const options = useMemo(
    () => connections.map((c) => ({ value: c.name, label: c.name })),
    [connections],
  )

  return (
    <div className="space-y-section-gap">
      <Breadcrumbs items={[{ label: 'Integrations', to: ROUTES.CONNECTIONS }, { label: 'Webhook receipts' }]} />
      <ListHeader title="Webhook receipts" count={entries.length} />

      <SearchFilterSort
        filters={
          <SelectField
            label="Connection"
            value={selected}
            onChange={setSelected}
            options={options}
          />
        }
      />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load webhook activity"
          description={error}
          onRetry={() => void reload()}
        />
      )}

      {loading && entries.length === 0 ? (
        <ProgressIndicator
          variant="indeterminate"
          label="Loading webhook activity"
          description={selected ? `Fetching receipts for ${selected}` : undefined}
        />
      ) : entries.length === 0 ? (
        connections.length === 0 ? (
          <EmptyState
            title="No connections configured"
            description="Configure a connection in the Integrations area before inspecting webhook deliveries."
          />
        ) : (
          <EmptyState
            title={selected ? 'No webhook deliveries yet' : 'Select a connection'}
            description={
              selected
                ? 'Inbound webhook events for this connection will appear here.'
                : 'Pick a connection from the dropdown above to inspect its receipt log.'
            }
          />
        )
      ) : (
        <SectionCard title="Recent receipts">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-text-secondary">
                  <th className="py-2 pr-4">Received</th>
                  <th className="py-2 pr-4">Event</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Processed</th>
                  <th className="py-2 pr-4">Error</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {entries.map((row) => (
                  <tr key={row.id}>
                    <td className="py-2 pr-4 font-mono text-xs">{formatDateTime(row.received_at)}</td>
                    <td className="py-2 pr-4 text-foreground">{row.event_type || '-'}</td>
                    <td className="py-2 pr-4">
                      <span className="inline-flex items-center gap-1.5">
                        <StatusBadge
                          status={mapWebhookStatus(row.status)}
                          decorative
                        />
                        <span className="text-xs uppercase text-text-secondary">
                          {row.status}
                        </span>
                      </span>
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs text-text-secondary">
                      {row.processed_at ? formatDateTime(row.processed_at) : '-'}
                    </td>
                    <td className="py-2 pr-4 truncate max-w-xs text-xs text-danger" title={row.error ?? undefined}>
                      {row.error}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </SectionCard>
      )}
    </div>
  )
}
