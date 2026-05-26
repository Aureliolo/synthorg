import { Inbox, MoreVertical, Plug, RefreshCw, Trash2 } from 'lucide-react'
import { Link } from 'react-router'
import type { Connection, HealthReport } from '@/api/types/integrations'
import { Button } from '@/components/ui/button'
import { ConnectionHealthBadge } from '@/components/ui/connection-health-badge'
import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/utils/format'
import { TypeBadge } from './TypeBadge'

function formatTimestamp(value: string | null): string {
  if (!value) return 'never'
  return formatRelativeTime(value)
}

export interface ConnectionCardProps {
  connection: Connection
  report: HealthReport | null
  checking: boolean
  onRunHealthCheck: () => void
  onEdit: () => void
  onDelete: () => void
  className?: string
}

function ConnectionCardActions({
  connection,
  checking,
  onRunHealthCheck,
  onEdit,
  onDelete,
}: Pick<ConnectionCardProps, 'connection' | 'checking' | 'onRunHealthCheck' | 'onEdit' | 'onDelete'>) {
  return (
    <div className="flex items-center gap-1">
      <Button
        type="button"
        size="icon"
        variant="ghost"
        aria-label={`Run health check for ${connection.name}`}
        onClick={onRunHealthCheck}
        disabled={checking}
      >
        <RefreshCw className={cn('size-4', checking && 'animate-spin')} aria-hidden />
      </Button>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        aria-label={`Edit ${connection.name}`}
        onClick={onEdit}
      >
        <MoreVertical className="size-4" aria-hidden />
      </Button>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        aria-label={`Delete ${connection.name}`}
        onClick={onDelete}
        className="text-danger hover:bg-danger/10 hover:text-danger"
      >
        <Trash2 className="size-4" aria-hidden />
      </Button>
    </div>
  )
}

function ConnectionCardMeta({
  connection,
  report,
  lastChecked,
}: {
  connection: Connection
  report: HealthReport | null
  lastChecked: string | null
}) {
  return (
    <div className="mt-3 flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <TypeBadge type={connection.connection_type} />
        <span className="text-xs text-text-muted">{connection.auth_method.replaceAll('_', ' ')}</span>
      </div>
      {connection.base_url && (
        <span className="truncate font-mono text-xs text-text-muted">{connection.base_url}</span>
      )}
      <div className="flex items-center gap-2 text-xs text-text-muted">
        <span>Checked {formatTimestamp(lastChecked)}</span>
        {report?.latency_ms != null && (
          <>
            <span>·</span>
            <span>{Math.round(report.latency_ms)} ms</span>
          </>
        )}
      </div>
      {report?.error_detail && <p className="line-clamp-2 text-xs text-danger">{report.error_detail}</p>}
      {/* Cross-link into the receipt inspector pre-selected on this
          connection (receipts are scoped per-connection there). */}
      <Link
        to={`/webhooks/receipts?connection=${encodeURIComponent(connection.name)}`}
        className="inline-flex items-center gap-1.5 pt-1 text-xs text-accent hover:underline"
      >
        <Inbox className="size-3.5" aria-hidden />
        View webhook receipts
      </Link>
    </div>
  )
}

export function ConnectionCard({
  connection,
  report,
  checking,
  onRunHealthCheck,
  onEdit,
  onDelete,
  className,
}: ConnectionCardProps) {
  const effectiveStatus = report?.status ?? connection.health_status ?? 'unknown'
  const lastChecked = report?.checked_at ?? connection.last_health_check_at ?? null

  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card p-card',
        'transition-all duration-200',
        'hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
        className,
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <Plug className="size-4 shrink-0 text-text-secondary" aria-hidden />
          <span className="truncate font-mono text-sm text-foreground">{connection.name}</span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ConnectionHealthBadge status={effectiveStatus} label pulse={checking} />
          <ConnectionCardActions
            connection={connection}
            checking={checking}
            onRunHealthCheck={onRunHealthCheck}
            onEdit={onEdit}
            onDelete={onDelete}
          />
        </div>
      </div>

      <ConnectionCardMeta connection={connection} report={report} lastChecked={lastChecked} />
    </div>
  )
}
