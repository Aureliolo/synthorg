/**
 * Drop-in section that composes ``VersionTimeline`` + ``VersionDiffDrawer``
 * + ``RollbackConfirmDialog`` against a generic ``VersionHistoryClient``.
 *
 * Owns local state (cursor pagination + selected version) so detail
 * pages do not have to thread per-domain Zustand slices for what is
 * essentially read-mostly history with an occasional rollback.  When
 * the host page also surfaces version data elsewhere (e.g. live
 * rollback notifications), promote the state to a domain store and
 * keep this component as a presentational helper.
 */
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import type {
  ReadOnlyVersionHistoryClient,
  VersionHistoryClient,
  VersionSnapshot,
} from '@/api/endpoints/version-history'
import { RollbackConfirmDialog } from './RollbackConfirmDialog'
import { VersionDiffDrawer } from './VersionDiffDrawer'
import { VersionTimeline, type TimelineItem } from './VersionTimeline'

const log = createLogger('version-history-section')

/**
 * Props discriminated on rollback capability so the type system
 * surfaces the rollback-capable client requirement at the call
 * site.  Read-only consumers pass a ``ReadOnlyVersionHistoryClient``
 * and OMIT (or set ``false`` on) ``rollbackSupported``;
 * rollback-capable consumers pass a full ``VersionHistoryClient``
 * AND set ``rollbackSupported: true``.
 */
export type VersionHistorySectionProps<T> =
  | (VersionHistorySectionBase & {
      client: VersionHistoryClient<T>
      rollbackSupported: true
      /**
       * Optional callback fired after a successful rollback so the
       * host page can refresh its primary data.
       */
      onAfterRollback?: () => void
    })
  | (VersionHistorySectionBase & {
      client: ReadOnlyVersionHistoryClient<T>
      rollbackSupported?: false
    })

interface VersionHistorySectionBase {
  /** Section heading shown above the timeline. */
  title: string
  /** Subtitle / hint copy shown beneath the heading. */
  description?: string
  /** Empty-state copy for first-load with zero versions. */
  emptyTitle?: string
  /** Empty-state secondary copy. */
  emptyDescription?: string
}

/**
 * Maps a snapshot row to the shape ``VersionTimeline`` expects.  We
 * keep the original snapshot in a sidecar map so click handlers can
 * recover the full payload without re-fetching.
 */
function toItem<T>(s: VersionSnapshot<T>): TimelineItem {
  return { id: s.id, version: s.version, created_at: s.created_at }
}

export function VersionHistorySection<T>(
  props: VersionHistorySectionProps<T>,
) {
  const {
    client,
    title,
    description,
    emptyTitle,
    emptyDescription,
  } = props
  const rollbackSupported = props.rollbackSupported === true
  const onAfterRollback = rollbackSupported ? props.onAfterRollback : undefined
  // Narrow ``client`` to the rollback-capable subtype only when the
  // host explicitly opted in; this keeps ``RollbackConfirmDialog``
  // (which requires ``rollback``) statically valid.
  const rollbackClient = rollbackSupported
    ? (client as VersionHistoryClient<T>)
    : null
  const [items, setItems] = useState<readonly VersionSnapshot<T>[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null)
  const [diffOpen, setDiffOpen] = useState(false)
  const [diffPair, setDiffPair] = useState<{
    from: number
    to: number
  } | null>(null)
  const [rollbackOpen, setRollbackOpen] = useState(false)
  // Bump to re-arm the guarded fetch effect.  Keeps the refresh
  // path inside the same cancellation-aware flow as the initial
  // load so a stale in-flight request cannot apply data after
  // ``client`` changes or the host unmounts.
  const [reloadNonce, setReloadNonce] = useState(0)

  // Initial load.  ``client`` is captured as the dependency; new
  // clients (rare; only when the parent rebuilds the factory)
  // trigger a fresh fetch with state reset.  We also clear
  // selection / diff / rollback state so cross-entity navigation
  // does not surface a stale rollback action against the previous
  // resource.
  useEffect(() => {
    let cancelled = false
    const run = async (): Promise<void> => {
      setLoading(true)
      setError(null)
      setItems([])
      setCursor(null)
      setHasMore(false)
      setSelectedVersion(null)
      setDiffOpen(false)
      setDiffPair(null)
      setRollbackOpen(false)
      try {
        const page = await client.list({ limit: 25 })
        if (cancelled) return
        setItems(page.data)
        setCursor(page.nextCursor)
        setHasMore(page.hasMore)
      } catch (err) {
        log.warn('list versions failed:', getErrorMessage(err))
        if (!cancelled) setError(getErrorMessage(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [client, reloadNonce])

  const handleLoadMore = async (): Promise<void> => {
    // Guard against fast repeated triggers re-requesting the same
    // cursor and appending duplicate rows.
    if (loadingMore || loading) return
    if (!hasMore || cursor === null) return
    setLoadingMore(true)
    try {
      const page = await client.list({ cursor, limit: 25 })
      setItems((prev) => [...prev, ...page.data])
      setCursor(page.nextCursor)
      setHasMore(page.hasMore)
    } catch (err) {
      log.warn('load more versions failed:', getErrorMessage(err))
      setError(getErrorMessage(err))
    } finally {
      setLoadingMore(false)
    }
  }

  const handleSelect = (item: VersionSnapshot<T>): void => {
    // Two-click compare: first click selects the "from" version,
    // second opens the diff drawer.  Clicking the same version twice
    // opens a no-op diff (timeline highlights the same row).
    if (selectedVersion === null) {
      setSelectedVersion(item.version)
      return
    }
    if (selectedVersion === item.version) {
      setSelectedVersion(null)
      return
    }
    const lower = Math.min(selectedVersion, item.version)
    const upper = Math.max(selectedVersion, item.version)
    setDiffPair({ from: lower, to: upper })
    setDiffOpen(true)
  }

  const handleRefresh = (): void => {
    // Bump the reload nonce so the guarded fetch effect re-runs.
    // Routing refresh through the effect (instead of an inline
    // unguarded async) ensures stale in-flight requests cannot
    // commit data after ``client`` changes or unmount.
    setReloadNonce((n) => n + 1)
  }

  return (
    <SectionCard title={title}>
      <div className="flex flex-col gap-grid-gap">
        {description !== undefined && (
          <p className="text-xs text-text-secondary">{description}</p>
        )}

        {error && (
          <ErrorBanner
            severity="error"
            title="Could not load version history"
            description={error}
            onRetry={handleRefresh}
          />
        )}

        <VersionTimeline
          items={items.map(toItem)}
          loading={loading}
          loadingMore={loadingMore}
          hasMore={hasMore}
          selectedVersion={selectedVersion}
          onSelect={(item) => {
            const original = items.find((i) => i.id === item.id)
            if (original !== undefined) handleSelect(original)
          }}
          onLoadMore={handleLoadMore}
          emptyTitle={emptyTitle}
          emptyDescription={emptyDescription}
        />

        {rollbackSupported && selectedVersion !== null && (
          <div className="flex justify-end gap-grid-gap pt-grid-gap">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setSelectedVersion(null)}
            >
              Clear selection
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={() => setRollbackOpen(true)}
            >
              Roll back to v{selectedVersion}
            </Button>
          </div>
        )}

        <VersionDiffDrawer<T>
          client={client}
          fromVersion={diffPair?.from ?? null}
          toVersion={diffPair?.to ?? null}
          open={diffOpen}
          onClose={() => setDiffOpen(false)}
        />

        {rollbackClient !== null && (
          <RollbackConfirmDialog<T>
            client={rollbackClient}
            toVersion={selectedVersion}
            open={rollbackOpen}
            onClose={() => setRollbackOpen(false)}
            onSuccess={() => {
              handleRefresh()
              onAfterRollback?.()
            }}
          />
        )}
      </div>
    </SectionCard>
  )
}
