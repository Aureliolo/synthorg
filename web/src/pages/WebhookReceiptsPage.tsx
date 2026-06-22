/**
 * Webhook receipt inspector.
 *
 * Per-connection list of recent inbound webhook deliveries. The
 * operator picks a connection from the existing connections store
 * and the page surfaces every received event with its status,
 * payload size, and any backend-captured error.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnimatePresence } from 'motion/react'
import { useSearchParams } from 'react-router'
import { ChevronDown, ChevronUp, RefreshCw } from 'lucide-react'
import { Breadcrumbs } from '@/components/ui/breadcrumbs'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { ProgressIndicator } from '@/components/ui/progress-indicator'
import { SectionCard } from '@/components/ui/section-card'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SelectField } from '@/components/ui/select-field'
import { StatusBadge } from '@/components/ui/status-badge'
import type { AgentRuntimeStatus } from '@/utils/agent-status'
import { useBulkSelection } from '@/hooks/useBulkSelection'
import { useConnectionsData } from '@/hooks/useConnectionsData'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { ROUTES } from '@/router/routes'
import { formatDateTime } from '@/utils/format'
import { getErrorMessage } from '@/utils/errors'
import {
  listWebhookActivity,
  retryWebhookReceipt,
  type WebhookReceipt,
} from '@/api/endpoints/webhooks'
import { WebhookRetryBar } from './webhooks/WebhookRetryBar'

const log = createLogger('WebhookReceiptsPage')

type AddToast = ReturnType<typeof useToastStore.getState>['add']
type WebhookSelection = ReturnType<typeof useBulkSelection>
type ConnectionList = ReturnType<typeof useConnectionsData>['connections']

/**
 * Cap on how many retry POSTs run concurrently. Webhook retries
 * re-publish to the bus and can trip per-connection rate limits; an
 * unbounded `Promise.all` over 100+ receipts could amplify a single
 * operator click into a thundering herd.
 */
const RETRY_CONCURRENCY = 4

// Backend ``WebhookReceipt.status`` is a free-form string; map known
// values onto the four-tone AgentRuntimeStatus the StatusBadge
// understands. Anything unrecognised falls back to ``idle``.
function mapWebhookStatus(status: string): AgentRuntimeStatus {
  const lower = status.toLowerCase()
  if (lower === 'delivered' || lower === 'processed' || lower === 'success') {
    return 'active'
  }
  if (lower === 'failed' || lower === 'error') return 'error'
  if (lower === 'rejected' || lower === 'cancelled') return 'offline'
  return 'idle'
}

/** Statuses eligible for retry. */
const RETRYABLE_STATUSES: ReadonlySet<string> = new Set(['failed', 'error', 'rejected'])

function isRetryable(receipt: WebhookReceipt): boolean {
  return RETRYABLE_STATUSES.has(receipt.status.toLowerCase())
}

function plural(n: number): string {
  return n === 1 ? '' : 's'
}

async function runBulkRetry(ids: readonly string[]): Promise<{ succeeded: number; failed: number }> {
  let succeeded = 0
  let failed = 0
  // Run retries in bounded-concurrency batches so a large selection
  // cannot saturate the API.
  for (let i = 0; i < ids.length; i += RETRY_CONCURRENCY) {
    const batch = ids.slice(i, i + RETRY_CONCURRENCY)
    const results = await Promise.allSettled(batch.map((id) => retryWebhookReceipt(id)))
    for (const result of results) {
      if (result.status === 'fulfilled') {
        succeeded += 1
      } else {
        failed += 1
        log.warn('Retry failed', { reason: sanitizeForLog(result.reason) })
      }
    }
  }
  return { succeeded, failed }
}

function bulkRetryToast(succeeded: number, failed: number, toast: AddToast): void {
  if (succeeded > 0 && failed === 0) {
    toast({ variant: 'success', title: `Retried ${succeeded} receipt${plural(succeeded)}` })
  } else if (succeeded > 0 && failed > 0) {
    toast({
      variant: 'warning',
      title: `Retried ${succeeded} of ${succeeded + failed}`,
      description: `${failed} retry attempt${plural(failed)} failed. Try those receipts again in a moment.`,
    })
  } else {
    toast({ variant: 'error', title: `Failed to retry ${failed} receipt${plural(failed)}` })
  }
}

function useWebhookConnectionSelect(connections: ConnectionList): {
  selected: string
  setSelected: (value: string) => void
  options: { value: string; label: string }[]
} {
  const [searchParams] = useSearchParams()
  const urlConnection = searchParams.get('connection') ?? ''
  const [selected, setSelected] = useState<string>('')

  // Resolve the active connection in one effect, so it does not depend on
  // `selected` and re-fire on its own writes. Precedence: a valid
  // URL-specified connection wins; otherwise keep a still-valid current
  // selection; otherwise fall back to the first connection. The microtask
  // defer keeps eslint-react's set-state-in-effect rule satisfied.
  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled) return
      setSelected((prev) => {
        if (urlConnection && connections.some((c) => c.name === urlConnection)) {
          return urlConnection
        }
        if (prev !== '' && connections.some((c) => c.name === prev)) return prev
        return connections[0]?.name ?? ''
      })
    })
    return () => {
      cancelled = true
    }
  }, [connections, urlConnection])

  const options = useMemo(
    () => connections.map((c) => ({ value: c.name, label: c.name })),
    [connections],
  )

  return { selected, setSelected, options }
}

interface WebhookActivity {
  entries: readonly WebhookReceipt[]
  loading: boolean
  error: string | null
  reload: () => Promise<void>
  loadMore: () => Promise<void>
  hasMore: boolean
  retryableIds: string[]
}

function useWebhookActivity(selected: string, selection: WebhookSelection): WebhookActivity {
  const [entries, setEntries] = useState<readonly WebhookReceipt[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)

  const requestSeqRef = useRef(0)
  const latestSelectedRef = useRef<string>(selected)
  latestSelectedRef.current = selected

  const reload = useCallback(async () => {
    if (!selected) return
    const requestedFor = selected
    requestSeqRef.current += 1
    const requestId = requestSeqRef.current
    setLoading(true)
    setError(null)
    function isStale(): boolean {
      return requestId !== requestSeqRef.current || latestSelectedRef.current !== requestedFor
    }
    try {
      const page = await listWebhookActivity(requestedFor)
      if (isStale()) return
      setEntries(page.data)
      setNextCursor(page.nextCursor)
      setHasMore(page.hasMore)
    } catch (err) {
      if (isStale()) return
      const message = getErrorMessage(err)
      log.error('listWebhookActivity failed', {
        connectionName: sanitizeForLog(requestedFor),
        error: sanitizeForLog(message),
      })
      setError(message)
    } finally {
      if (!isStale()) setLoading(false)
    }
  }, [selected])

  // Follow the opaque cursor to append the next page, mirroring the
  // ProjectBrainPage load-more pattern. The retained ``nextCursor`` /
  // ``hasMore`` from the prior fetch drive the control; an early return
  // covers the already-drained case.
  const loadMore = useCallback(async () => {
    if (!selected || !hasMore || nextCursor === null) return
    const requestedFor = selected
    requestSeqRef.current += 1
    const requestId = requestSeqRef.current
    setLoading(true)
    function isStale(): boolean {
      return requestId !== requestSeqRef.current || latestSelectedRef.current !== requestedFor
    }
    try {
      const page = await listWebhookActivity(requestedFor, { cursor: nextCursor })
      if (isStale()) return
      setEntries((prev) => [...prev, ...page.data])
      setNextCursor(page.nextCursor)
      setHasMore(page.hasMore)
    } catch (err) {
      if (isStale()) return
      const message = getErrorMessage(err)
      log.error('listWebhookActivity load-more failed', {
        connectionName: sanitizeForLog(requestedFor),
        error: sanitizeForLog(message),
      })
      setError(message)
    } finally {
      if (!isStale()) setLoading(false)
    }
  }, [selected, hasMore, nextCursor])

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled) return
      setEntries([])
      setNextCursor(null)
      setHasMore(false)
      selection.clear()
    })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- clears state only when `selected` changes; `selection` (memoised by useBulkSelection) changes whenever selectedIds mutates, so listing it would re-run the clear loop after every selection toggle
  }, [selected])

  useEffect(() => {
    void reload()
  }, [reload])

  const retryableIds = useMemo(
    () => entries.filter(isRetryable).map((row) => row.id),
    [entries],
  )

  return { entries, loading, error, reload, loadMore, hasMore, retryableIds }
}

function useWebhookRetry({
  retryableIds,
  selection,
  reload,
  toast,
}: {
  retryableIds: string[]
  selection: WebhookSelection
  reload: () => Promise<void>
  toast: AddToast
}): { retrying: boolean; handleBulkRetry: () => Promise<void> } {
  const [retrying, setRetrying] = useState(false)

  const handleBulkRetry = useCallback(async () => {
    const retryableSet = new Set(retryableIds)
    const ids = [...selection.selectedIds].filter((id) => retryableSet.has(id))
    if (ids.length === 0) return
    setRetrying(true)
    const { succeeded, failed } = await runBulkRetry(ids)
    setRetrying(false)
    selection.clear()
    bulkRetryToast(succeeded, failed, toast)
    await reload()
  }, [retryableIds, reload, selection, toast])

  return { retrying, handleBulkRetry }
}

function WebhookReceiptRow({ row, selection }: { row: WebhookReceipt; selection: WebhookSelection }) {
  const retryable = isRetryable(row)
  return (
    <tr>
      <td className="py-2 pr-4">
        <input
          type="checkbox"
          aria-label={`Select receipt ${row.id}`}
          checked={selection.selectedIds.has(row.id)}
          onChange={() => selection.toggle(row.id)}
          disabled={!retryable}
        />
      </td>
      <td className="py-2 pr-4 font-mono text-xs">{formatDateTime(row.received_at)}</td>
      <td className="py-2 pr-4 text-foreground">{row.event_type || '-'}</td>
      <td className="py-2 pr-4">
        <span className="inline-flex items-center gap-1.5">
          <StatusBadge status={mapWebhookStatus(row.status)} decorative />
          <span className="text-xs uppercase text-text-secondary">{row.status}</span>
        </span>
      </td>
      <td className="py-2 pr-4 font-mono text-xs text-text-secondary">
        {row.processed_at ? formatDateTime(row.processed_at) : '-'}
      </td>
      <td className="py-2 pr-4 truncate max-w-xs text-xs text-danger" title={row.error ?? undefined}>
        {row.error}
      </td>
    </tr>
  )
}

function WebhookReceiptsTable({
  entries,
  selection,
  retryableIds,
}: {
  entries: readonly WebhookReceipt[]
  selection: WebhookSelection
  retryableIds: string[]
}) {
  const [receivedSortDir, setReceivedSortDir] = useState<'asc' | 'desc'>('desc')

  const sortedEntries = useMemo(() => {
    const factor = receivedSortDir === 'asc' ? 1 : -1
    const at = (r: WebhookReceipt) => new Date(r.received_at).getTime()
    return [...entries].sort((a, b) => (at(a) - at(b)) * factor)
  }, [entries, receivedSortDir])
  const receivedSortLabel = receivedSortDir === 'asc' ? 'ascending' : 'descending'

  return (
    <SectionCard title="Recent receipts">
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-text-secondary">
              <th className="py-2 pr-4">
                <input
                  type="checkbox"
                  aria-label="Select all retryable receipts"
                  checked={selection.isAllSelected(retryableIds)}
                  ref={(el) => {
                    if (el) el.indeterminate = selection.isPartiallySelected(retryableIds)
                  }}
                  onChange={() => selection.toggleAll(retryableIds)}
                  disabled={retryableIds.length === 0}
                />
              </th>
              <th className="py-2 pr-4" aria-sort={receivedSortLabel}>
                <button
                  type="button"
                  onClick={() => setReceivedSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'))}
                  className="inline-flex items-center gap-1 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded"
                  aria-label={`Sort by received time (${receivedSortLabel})`}
                >
                  Received
                  {receivedSortDir === 'asc' ? (
                    <ChevronUp className="size-3" aria-hidden="true" />
                  ) : (
                    <ChevronDown className="size-3" aria-hidden="true" />
                  )}
                </button>
              </th>
              <th className="py-2 pr-4">Event</th>
              <th className="py-2 pr-4">Status</th>
              <th className="py-2 pr-4">Processed</th>
              <th className="py-2 pr-4">Error</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {sortedEntries.map((row) => (
              <WebhookReceiptRow key={row.id} row={row} selection={selection} />
            ))}
          </tbody>
        </table>
      </div>
    </SectionCard>
  )
}

interface WebhookReceiptsContentProps {
  loading: boolean
  entries: readonly WebhookReceipt[]
  connectionsCount: number
  selected: string
  selection: WebhookSelection
  retryableIds: string[]
  hasMore: boolean
  onLoadMore: () => void
}

function WebhookReceiptsContent({
  loading,
  entries,
  connectionsCount,
  selected,
  selection,
  retryableIds,
  hasMore,
  onLoadMore,
}: WebhookReceiptsContentProps) {
  if (loading && entries.length === 0) {
    return (
      <ProgressIndicator
        variant="indeterminate"
        label="Loading webhook activity"
        description={selected ? `Fetching receipts for ${selected}` : undefined}
      />
    )
  }
  if (entries.length === 0) {
    if (connectionsCount === 0) {
      return (
        <EmptyState
          title="No connections configured"
          description="Configure a connection in the Integrations area before inspecting webhook deliveries."
        />
      )
    }
    return (
      <EmptyState
        title={selected ? 'No webhook deliveries yet' : 'Select a connection'}
        description={
          selected
            ? 'Inbound webhook events for this connection will appear here.'
            : 'Pick a connection from the dropdown above to inspect its receipt log.'
        }
      />
    )
  }
  return (
    <div className="space-y-section-gap">
      <WebhookReceiptsTable entries={entries} selection={selection} retryableIds={retryableIds} />
      {hasMore && (
        <Button
          variant="outline"
          size="sm"
          onClick={onLoadMore}
          disabled={loading}
          className="gap-1"
        >
          <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} aria-hidden="true" />
          {loading ? 'Loading more…' : 'Load more'}
        </Button>
      )}
    </div>
  )
}

export default function WebhookReceiptsPage() {
  const { connections } = useConnectionsData()
  const toast = useToastStore((s) => s.add)
  const selection = useBulkSelection()
  const { selected, setSelected, options } = useWebhookConnectionSelect(connections)
  const { entries, loading, error, reload, loadMore, hasMore, retryableIds } = useWebhookActivity(
    selected,
    selection,
  )
  const { retrying, handleBulkRetry } = useWebhookRetry({ retryableIds, selection, reload, toast })

  return (
    <div className="space-y-section-gap">
      <Breadcrumbs items={[{ label: 'Integrations', to: ROUTES.CONNECTIONS }, { label: 'Webhook receipts' }]} />
      <ListHeader title="Webhook receipts" count={entries.length} />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load webhook activity"
          description={error}
          onRetry={() => void reload()}
        />
      )}

      <SearchFilterSort
        filters={
          <SelectField label="Connection" value={selected} onChange={setSelected} options={options} />
        }
      />

      <WebhookReceiptsContent
        loading={loading}
        entries={entries}
        connectionsCount={connections.length}
        selected={selected}
        selection={selection}
        retryableIds={retryableIds}
        hasMore={hasMore}
        onLoadMore={() => void loadMore()}
      />

      <AnimatePresence mode="wait">
        {selection.count > 0 && (
          <WebhookRetryBar
            key="retry-bar"
            count={selection.count}
            retrying={retrying}
            onClear={selection.clear}
            onRetry={() => void handleBulkRetry()}
          />
        )}
      </AnimatePresence>
    </div>
  )
}
