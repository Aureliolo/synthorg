/**
 * Drop-in section that composes ``VersionTimeline`` + ``VersionDiffDrawer``
 * + ``RollbackConfirmDialog`` against a generic ``VersionHistoryClient``.
 *
 * Owns local state (cursor pagination + selected version) via the
 * ``useVersionHistory`` hook so detail pages do not have to thread
 * per-domain Zustand slices for what is essentially read-mostly
 * history with an occasional rollback. When the host page also
 * surfaces version data elsewhere (e.g. live rollback notifications),
 * promote the state to a domain store and keep this component as a
 * presentational helper.
 */
import { Button } from '@/components/ui/button'
import { ErrorBanner } from '@/components/ui/error-banner'
import { SectionCard } from '@/components/ui/section-card'
import type {
  ReadOnlyVersionHistoryClient,
  VersionHistoryClient,
  VersionSnapshot,
} from '@/api/endpoints/version-history'
import { RollbackConfirmDialog } from './RollbackConfirmDialog'
import { useVersionHistory, type VersionHistoryHandle } from './useVersionHistory'
import { VersionDiffDrawer } from './VersionDiffDrawer'
import { VersionTimeline, type TimelineItem } from './VersionTimeline'

/**
 * Props discriminated on rollback capability so the type system
 * surfaces the rollback-capable client requirement at the call
 * site. Read-only consumers pass a ``ReadOnlyVersionHistoryClient``
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
 * Maps a snapshot row to the shape ``VersionTimeline`` expects. We
 * keep the original snapshot in a sidecar map so click handlers can
 * recover the full payload without re-fetching.
 */
function toItem<T>(s: VersionSnapshot<T>): TimelineItem {
  return { id: s.id, version: s.version, created_at: s.created_at }
}

export interface RollbackToolbarProps {
  selectedVersion: number
  onClear: () => void
  onConfirm: () => void
}

function RollbackToolbar({
  selectedVersion,
  onClear,
  onConfirm,
}: RollbackToolbarProps) {
  return (
    <div className="flex justify-end gap-grid-gap pt-grid-gap">
      <Button variant="secondary" size="sm" onClick={onClear}>
        Clear selection
      </Button>
      <Button variant="destructive" size="sm" onClick={onConfirm}>
        Roll back to v{selectedVersion}
      </Button>
    </div>
  )
}

function _resolveRollbackClient<T>(
  props: VersionHistorySectionProps<T>,
): VersionHistoryClient<T> | null {
  // Narrow ``client`` to the rollback-capable subtype only when the
  // host explicitly opted in; this keeps ``RollbackConfirmDialog``
  // (which requires ``rollback``) statically valid.
  return props.rollbackSupported === true ? props.client : null
}

export interface VersionHistoryBodyProps<T> {
  history: VersionHistoryHandle<T>
  client: ReadOnlyVersionHistoryClient<T>
  rollbackClient: VersionHistoryClient<T> | null
  onAfterRollback: (() => void) | undefined
  emptyTitle: string | undefined
  emptyDescription: string | undefined
}

function VersionHistoryBody<T>({
  history,
  client,
  rollbackClient,
  onAfterRollback,
  emptyTitle,
  emptyDescription,
}: VersionHistoryBodyProps<T>) {
  const refresh = (): void => { void history.refresh() }
  const selectFromTimeline = (item: TimelineItem): void => {
    const original = history.findById(item.id)
    if (original) history.select(original)
  }
  const handleRollbackSuccess = (): void => {
    refresh()
    onAfterRollback?.()
  }
  return (
    <>
      <VersionTimeline
        items={history.items.map(toItem)}
        loading={history.loading}
        loadingMore={history.loadingMore}
        hasMore={history.hasMore}
        selectedVersion={history.selectedVersion}
        onSelect={selectFromTimeline}
        onLoadMore={() => { void history.loadMore() }}
        emptyTitle={emptyTitle}
        emptyDescription={emptyDescription}
      />
      {rollbackClient && history.selectedVersion !== null && (
        <RollbackToolbar
          selectedVersion={history.selectedVersion}
          onClear={history.clearSelection}
          onConfirm={history.openRollback}
        />
      )}
      <VersionDiffDrawer<T>
        client={client}
        fromVersion={history.diffFrom}
        toVersion={history.diffTo}
        open={history.diffOpen}
        onClose={history.closeDiff}
      />
      {rollbackClient && (
        <RollbackConfirmDialog<T>
          client={rollbackClient}
          toVersion={history.selectedVersion}
          open={history.rollbackOpen}
          onClose={history.closeRollback}
          onSuccess={handleRollbackSuccess}
        />
      )}
    </>
  )
}

export function VersionHistorySection<T>(
  props: VersionHistorySectionProps<T>,
) {
  const { client, title, description, emptyTitle, emptyDescription } = props
  const rollbackClient = _resolveRollbackClient(props)
  const onAfterRollback = props.rollbackSupported === true
    ? props.onAfterRollback
    : undefined
  const history = useVersionHistory(client)
  return (
    <SectionCard title={title}>
      <div className="flex flex-col gap-grid-gap">
        {description !== undefined && (
          <p className="text-xs text-text-secondary">{description}</p>
        )}
        {history.error && (
          <ErrorBanner
            severity="error"
            title="Could not load version history"
            description={history.error}
            onRetry={() => { void history.refresh() }}
          />
        )}
        <VersionHistoryBody
          history={history}
          client={client}
          rollbackClient={rollbackClient}
          onAfterRollback={onAfterRollback}
          emptyTitle={emptyTitle}
          emptyDescription={emptyDescription}
        />
      </div>
    </SectionCard>
  )
}
