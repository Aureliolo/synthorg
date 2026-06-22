import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'
import { listWebhookActivity, type WebhookReceipt } from '@/api/endpoints/webhooks'
import type { useBulkSelection } from '@/hooks/useBulkSelection'

const log = createLogger('WebhookReceiptsPage')

type WebhookSelection = ReturnType<typeof useBulkSelection>

/** Statuses eligible for retry. */
const RETRYABLE_STATUSES: ReadonlySet<string> = new Set(['failed', 'error', 'rejected'])

export function isRetryable(receipt: WebhookReceipt): boolean {
  return RETRYABLE_STATUSES.has(receipt.status.toLowerCase())
}

export interface WebhookActivity {
  entries: readonly WebhookReceipt[]
  loading: boolean
  error: string | null
  reload: () => Promise<void>
  loadMore: () => Promise<void>
  hasMore: boolean
  retryableIds: string[]
}

/**
 * Per-connection webhook-receipt data hook with opaque cursor pagination.
 *
 * ``reload`` fetches the first page; ``loadMore`` follows the retained
 * ``nextCursor`` and appends, early-returning when the list is drained.
 * Both share a single sequence-guarded fetch so a stale connection switch
 * cannot clobber a newer request's results.
 */
export function useWebhookActivity(
  selected: string,
  selection: WebhookSelection,
): WebhookActivity {
  const [entries, setEntries] = useState<readonly WebhookReceipt[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [nextCursor, setNextCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)

  const requestSeqRef = useRef(0)
  const latestSelectedRef = useRef<string>(selected)
  latestSelectedRef.current = selected

  const fetchPage = useCallback(
    async (cursor: string | null, append: boolean): Promise<void> => {
      if (!selected) return
      const requestedFor = selected
      requestSeqRef.current += 1
      const requestId = requestSeqRef.current
      setLoading(true)
      if (!append) setError(null)
      const isStale = (): boolean =>
        requestId !== requestSeqRef.current || latestSelectedRef.current !== requestedFor
      try {
        const page = await listWebhookActivity(
          requestedFor,
          cursor === null ? undefined : { cursor },
        )
        if (isStale()) return
        setEntries((prev) => (append ? [...prev, ...page.data] : page.data))
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
    },
    [selected],
  )

  const reload = useCallback(() => fetchPage(null, false), [fetchPage])
  const loadMore = useCallback(() => {
    if (!hasMore || nextCursor === null) return Promise.resolve()
    return fetchPage(nextCursor, true)
  }, [fetchPage, hasMore, nextCursor])

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
