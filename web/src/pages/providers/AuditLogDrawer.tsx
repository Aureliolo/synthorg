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

function summariseEvent(event: ProviderAuditEvent): string {
  const payload = event.payload ?? {}
  if (event.event_type === 'model_added' || event.event_type === 'model_removed') {
    const id = payload.model_id
    return typeof id === 'string' ? id : ''
  }
  if (event.event_type === 'model_config_updated') {
    const fields = payload.fields_changed
    return Array.isArray(fields) ? fields.map(String).join(', ') : ''
  }
  if (event.event_type === 'provider_updated') {
    const fields = payload.fields_changed
    return Array.isArray(fields) ? fields.map(String).join(', ') : ''
  }
  if (event.event_type === 'models_synced') {
    const a = payload.added_count
    const r = payload.removed_count
    const u = payload.updated_count
    return `+${typeof a === 'number' ? a : 0} / -${typeof r === 'number' ? r : 0} / ~${typeof u === 'number' ? u : 0}`
  }
  if (event.event_type === 'provider_credentials_rotated') {
    const masked = payload.masked_secret
    return typeof masked === 'string' ? masked : ''
  }
  return ''
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
          {EVENT_LABEL[event.event_type] ?? event.event_type}
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

export function AuditLogDrawer({ providerName, open, onClose }: AuditLogDrawerProps) {
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
  const isActiveProvider =
    providerName !== null && auditProviderName === providerName
  const visibleEvents = isActiveProvider ? events : []
  const visibleError = isActiveProvider ? error : null
  const visibleHasMore = isActiveProvider && hasMore

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

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="Audit log"
      ariaLabel="Provider mutation audit log"
      width="default"
    >
      <div className="flex flex-col gap-grid-gap p-card">
        {visibleError && (
          <ErrorBanner
            severity="error"
            title="Failed to load audit log"
            description={visibleError}
            onRetry={
              providerName
                ? () => {
                    void fetchAudit(providerName)
                  }
                : undefined
            }
          />
        )}

        {loading && (
          <div className="flex flex-col gap-grid-gap">
            {[1, 2, 3, 4, 5].map((idx) => (
              <Skeleton key={idx} className="h-14 w-full" />
            ))}
          </div>
        )}

        {!loading && !visibleError && visibleEvents.length === 0 && (
          <EmptyState
            title="No audit events"
            description="Mutations to this provider will appear here."
          />
        )}

        {!loading && visibleEvents.length > 0 && (
          <ol className="flex flex-col divide-y divide-border">
            {visibleEvents.map((event) => (
              <AuditRowItem
                key={event.id ?? `${event.provider_name}-${event.occurred_at}`}
                event={event}
              />
            ))}
          </ol>
        )}

        {visibleHasMore && (
          <Button
            variant="secondary"
            onClick={() => {
              void fetchMoreAudit()
            }}
            disabled={loadingMore}
          >
            {loadingMore ? 'Loading…' : 'Load more'}
          </Button>
        )}
      </div>
    </Drawer>
  )
}
