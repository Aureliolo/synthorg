import { useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Drawer } from '@/components/ui/drawer'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { Skeleton } from '@/components/ui/skeleton'
import { useProvidersStore } from '@/stores/providers'
import { formatDateTime } from '@/utils/format'
import type {
  ProviderAuditEvent,
  ProviderAuditEventType,
} from '@/api/types/providers'

interface AuditLogDrawerProps {
  providerName: string | null
  open: boolean
  onClose: () => void
}

const EVENT_LABEL: Record<ProviderAuditEventType, string> = {
  provider_created: 'Created',
  provider_updated: 'Updated',
  provider_deleted: 'Deleted',
  provider_credentials_rotated: 'Credentials rotated',
  provider_rate_limits_updated: 'Rate limits updated',
  preset_override_updated: 'Preset override',
  model_added: 'Model added',
  model_removed: 'Model removed',
  model_config_updated: 'Model config',
  model_pulled: 'Model pulled',
  models_synced: 'Models synced',
}

type AuditPayload = NonNullable<ProviderAuditEvent['payload']>

function summariseModelId(payload: AuditPayload): string {
  const id = payload.model_id
  return typeof id === 'string' ? id : ''
}

function summariseFieldsChanged(payload: AuditPayload): string {
  const fields = payload.fields_changed
  return Array.isArray(fields) ? fields.map(String).join(', ') : ''
}

function summariseSyncCounts(payload: AuditPayload): string {
  const a = payload.added_count
  const r = payload.removed_count
  const u = payload.updated_count
  return `+${typeof a === 'number' ? a : 0} / -${typeof r === 'number' ? r : 0} / ~${typeof u === 'number' ? u : 0}`
}

function summariseCredentials(payload: AuditPayload): string {
  const masked = payload.masked_secret
  return typeof masked === 'string' ? masked : ''
}

// Table-driven dispatch keyed by audit event type keeps summariseEvent
// under the complexity cap; unmapped event types render an empty summary.
const EVENT_SUMMARISERS: Partial<
  Record<ProviderAuditEventType, (payload: AuditPayload) => string>
> = {
  model_added: summariseModelId,
  model_removed: summariseModelId,
  model_config_updated: summariseFieldsChanged,
  provider_updated: summariseFieldsChanged,
  models_synced: summariseSyncCounts,
  provider_credentials_rotated: summariseCredentials,
}

function summariseEvent(event: ProviderAuditEvent): string {
  return EVENT_SUMMARISERS[event.event_type]?.(event.payload) ?? ''
}

/**
 * Drawer for the provider mutation audit log.  Cursor-paginated;
 * "Load more" is rendered while ``has_more`` is true.  The list is
 * read-only and ordered newest-first by the backend.
 */
/**
 * One audit log row.  Extracted so the ``.map()`` body in
 * ``AuditLogDrawer`` stays simple and the row's structure can be
 * tested / restyled independently.
 */
function AuditRowItem({ event }: { event: ProviderAuditEvent }) {
  return (
    <li className="flex flex-col gap-1 py-grid-gap">
      <div className="flex items-center justify-between gap-grid-gap">
        <span className="font-medium text-foreground">
          {EVENT_LABEL[event.event_type]}
        </span>
        <time
          dateTime={event.occurred_at}
          className="text-xs text-text-secondary"
        >
          {formatDateTime(event.occurred_at)}
        </time>
      </div>
      <div className="text-sm text-text-secondary">
        {summariseEvent(event)}
      </div>
      <div className="text-xs text-text-tertiary">
        by {event.actor.label}
      </div>
    </li>
  )
}

interface AuditLogView {
  visibleEvents: readonly ProviderAuditEvent[]
  visibleError: string | null
  visibleHasMore: boolean
  loading: boolean
  loadingMore: boolean
  fetchAudit: ReturnType<typeof useProvidersStore.getState>['fetchAudit']
  fetchMoreAudit: ReturnType<typeof useProvidersStore.getState>['fetchMoreAudit']
}

function useAuditLogView(providerName: string | null, open: boolean): AuditLogView {
  const events = useProvidersStore((s) => s.auditEvents)
  const loading = useProvidersStore((s) => s.auditLoading)
  const loadingMore = useProvidersStore((s) => s.auditLoadingMore)
  const error = useProvidersStore((s) => s.auditError)
  const hasMore = useProvidersStore((s) => s.auditHasMore)
  const auditProviderName = useProvidersStore((s) => s.auditProviderName)
  const fetchAudit = useProvidersStore((s) => s.fetchAudit)
  const fetchMoreAudit = useProvidersStore((s) => s.fetchMoreAudit)
  const clearAudit = useProvidersStore((s) => s.clearAudit)

  // Gate every read-state surface on the audit slice belonging to
  // the active provider.  The store's stale-response guards prevent
  // a slow fetch from overwriting the wrong provider's events, but
  // until the new fetch completes the drawer would still render
  // events / hasMore / error from the previous provider.
  const isActiveProvider = providerName !== null && auditProviderName === providerName

  useEffect(() => {
    if (open && providerName) {
      void fetchAudit(providerName)
    } else {
      // Clear whenever the drawer is closed OR open without a
      // valid provider name; otherwise stale audit data from the
      // previous provider lingers visibly until the next fetch.
      clearAudit()
    }
  }, [open, providerName, fetchAudit, clearAudit])

  return {
    visibleEvents: isActiveProvider ? events : [],
    visibleError: isActiveProvider ? error : null,
    visibleHasMore: isActiveProvider && hasMore,
    loading,
    loadingMore,
    fetchAudit,
    fetchMoreAudit,
  }
}

function AuditErrorBanner({
  visibleError,
  providerName,
  onRetry,
}: {
  visibleError: string | null
  providerName: string | null
  onRetry: (name: string) => void
}) {
  if (!visibleError) return null
  return (
    <ErrorBanner
      severity="error"
      title="Failed to load audit log"
      description={visibleError}
      onRetry={
        providerName
          ? () => {
              onRetry(providerName)
            }
          : undefined
      }
    />
  )
}

function AuditLogList({
  loading,
  visibleError,
  visibleEvents,
}: {
  loading: boolean
  visibleError: string | null
  visibleEvents: readonly ProviderAuditEvent[]
}) {
  if (loading) {
    return (
      <div className="flex flex-col gap-grid-gap">
        {[1, 2, 3, 4, 5].map((idx) => (
          <Skeleton key={idx} className="h-14 w-full" />
        ))}
      </div>
    )
  }

  if (!visibleError && visibleEvents.length === 0) {
    return (
      <EmptyState
        title="No audit events"
        description="Mutations to this provider will appear here."
      />
    )
  }

  if (visibleEvents.length > 0) {
    return (
      <ol className="flex flex-col divide-y divide-border">
        {visibleEvents.map((event) => (
          <AuditRowItem
            key={event.id ?? `${event.provider_name}-${event.occurred_at}`}
            event={event}
          />
        ))}
      </ol>
    )
  }

  return null
}

function AuditLoadMore({
  visibleHasMore,
  loadingMore,
  onLoadMore,
}: {
  visibleHasMore: boolean
  loadingMore: boolean
  onLoadMore: () => void
}) {
  if (!visibleHasMore) return null
  return (
    <Button
      variant="secondary"
      onClick={() => {
        onLoadMore()
      }}
      disabled={loadingMore}
    >
      {loadingMore ? 'Loading…' : 'Load more'}
    </Button>
  )
}

function AuditLogBody({
  view,
  providerName,
}: {
  view: AuditLogView
  providerName: string | null
}) {
  const { visibleEvents, visibleError, visibleHasMore, loading, loadingMore } = view
  return (
    <div className="flex flex-col gap-grid-gap p-card">
      <AuditErrorBanner
        visibleError={visibleError}
        providerName={providerName}
        onRetry={view.fetchAudit}
      />
      <AuditLogList loading={loading} visibleError={visibleError} visibleEvents={visibleEvents} />
      <AuditLoadMore
        visibleHasMore={visibleHasMore}
        loadingMore={loadingMore}
        onLoadMore={view.fetchMoreAudit}
      />
    </div>
  )
}

export function AuditLogDrawer({ providerName, open, onClose }: AuditLogDrawerProps) {
  const view = useAuditLogView(providerName, open)

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Audit log"
      ariaLabel="Provider mutation audit log"
      width="default"
    >
      <AuditLogBody view={view} providerName={providerName} />
    </Drawer>
  )
}
