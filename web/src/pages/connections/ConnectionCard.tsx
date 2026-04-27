import { MoreVertical, Plug, RefreshCw } from 'lucide-react'
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

export function ConnectionCard({
  connection,
  report,
  checking,
  onRunHealthCheck,
  onEdit,
  onDelete,
  className,
}: ConnectionCardProps) {
  const effectiveStatus = report?.status ?? connection.health_status
  const lastChecked = report?.checked_at ?? connection.last_health_check_at

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
          <span className="truncate font-mono text-sm text-foreground">
            {connection.name}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <ConnectionHealthBadge status={effectiveStatus} label pulse={checking} />
          <div className="flex items-center gap-1">
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label={`Run health check for ${connection.name}`}
              onClick={onRunHealthCheck}
              disabled={checking}
            >
              <RefreshCw
                className={cn('size-4', checking && 'animate-spin')}
                aria-hidden
              />
            </Button>
            <Button
              type="button"
              size="icon"
              variant="ghost"
              aria-label={`More actions for ${connection.name}`}
              onClick={onEdit}
            >
              <MoreVertical className="size-4" aria-hidden />
            </Button>
          </div>
        </div>
      </div>

      <div className="mt-3 flex flex-col gap-1.5">
        <div className="flex items-center gap-2">
          <TypeBadge type={connection.connection_type} />
          <span className="text-xs text-text-muted">
            {connection.auth_method.replaceAll('_', ' ')}
          </span>
        </div>
        {connection.base_url && (
          <span className="truncate font-mono text-xs text-text-muted">
            {connection.base_url}
          </span>
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
        {report?.error_detail && (
          <p className="line-clamp-2 text-xs text-danger">{report.error_detail}</p>
        )}
      </div>

      <div className="mt-3 flex justify-end">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onDelete}
          className="text-danger hover:text-danger"
        >
          Delete
        </Button>
      </div>
    </div>
  )
}
