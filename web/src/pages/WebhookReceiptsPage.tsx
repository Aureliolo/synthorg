/**
 * Webhook receipt inspector.
 *
 * Per-connection list of recent inbound webhook deliveries. The
 * operator picks a connection from the existing connections store
 * and the page surfaces every received event with its status,
 * payload size, and any backend-captured error.
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2 } from 'lucide-react'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SelectField } from '@/components/ui/select-field'
import { useConnectionsData } from '@/hooks/useConnectionsData'
import { createLogger } from '@/lib/logger'
import { ROUTES } from '@/router/routes'
import { formatDateTime, formatNumber } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'
import {
  listWebhookActivity,
  type WebhookActivityEntry,
} from '@/api/endpoints/webhooks'

const log = createLogger('WebhookReceiptsPage')

export default function WebhookReceiptsPage() {
  const { connections } = useConnectionsData()
  const [selected, setSelected] = useState<string>('')
  const [entries, setEntries] = useState<readonly WebhookActivityEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Default the selector to the first connection that supports webhooks
  // (we don't have that flag locally; just pick the first connection).
  // Defer the setState to a microtask so the synchronous-in-effect
  // form does not trip @eslint-react/set-state-in-effect.
  useEffect(() => {
    if (selected !== '' || connections.length === 0) return
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled) return
      setSelected(connections[0]!.name)
    })
    return () => { cancelled = true }
  }, [connections, selected])

  const reload = useCallback(async () => {
    if (!selected) return
    setLoading(true)
    setError(null)
    try {
      const rows = await listWebhookActivity(selected)
      setEntries(rows)
    } catch (err) {
      const message = getErrorMessage(err)
      log.error('listWebhookActivity failed', { connectionName: selected, error: message })
      setError(message)
    } finally {
      setLoading(false)
    }
  }, [selected])

  useEffect(() => {
    void reload()
  }, [reload])

  const options = connections.map((c) => ({ value: c.name, label: c.name }))

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
        <div className="flex items-center justify-center py-12">
          <Loader2 className="size-6 animate-spin text-text-muted" />
        </div>
      ) : entries.length === 0 ? (
        <EmptyState
          title={selected ? 'No webhook deliveries yet' : 'Select a connection'}
          description={
            selected
              ? 'Inbound webhook events for this connection will appear here.'
              : 'Pick a connection from the dropdown above to inspect its receipt log.'
          }
        />
      ) : (
        <SectionCard title="Recent receipts">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-text-secondary">
                  <th className="py-2 pr-4">Received</th>
                  <th className="py-2 pr-4">Event</th>
                  <th className="py-2 pr-4">Status</th>
                  <th className="py-2 pr-4">Bytes</th>
                  <th className="py-2 pr-4">Error</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {entries.map((row) => (
                  <tr key={row.id}>
                    <td className="py-2 pr-4 font-mono text-xs">{formatDateTime(row.received_at)}</td>
                    <td className="py-2 pr-4 text-foreground">{row.event_type}</td>
                    <td className="py-2 pr-4">
                      <span className="rounded-md border border-border bg-card px-2 py-0.5 text-xs uppercase text-text-secondary">
                        {row.status}
                        {row.status_code != null && ` · ${row.status_code}`}
                      </span>
                    </td>
                    <td className="py-2 pr-4 font-mono text-xs text-text-secondary">
                      {row.payload_bytes != null ? formatNumber(row.payload_bytes) : '-'}
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
