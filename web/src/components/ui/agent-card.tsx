import { useId } from 'react'
import { cn } from '@/lib/utils'
import type { AgentRuntimeStatus } from '@/utils/agent-status'
import { formatDateTime } from '@/utils/format'
import { Avatar } from './avatar'
import { StatusBadge } from './status-badge'

export interface AgentCardProps {
  name: string
  role: string
  department: string
  status: AgentRuntimeStatus
  /** Resolved model identifier (e.g. "example-large-001"). */
  model?: string | undefined
  /** Resolved capability tier. */
  tier?: 'large' | 'medium' | 'small' | null | undefined
  currentTask?: string | undefined
  /** Human-readable (usually relative) timestamp text shown in the footer. */
  timestamp?: string | undefined
  /**
   * Machine-readable ISO datetime backing the footer timestamp. When set the
   * footer renders a `<time>` element whose `title` exposes the absolute
   * value, so a relative label like "3 days ago" still surfaces the exact
   * instant on hover.
   */
  timestampIso?: string | undefined
  className?: string | undefined
  /** Inline style for flash animation (from useFlash). */
  flashStyle?: React.CSSProperties | undefined
}

export function AgentCard({
  name,
  role,
  department,
  status,
  model,
  tier,
  currentTask,
  timestamp,
  timestampIso,
  className,
  flashStyle,
}: AgentCardProps) {
  const nameId = useId()
  const roleId = useId()
  return (
    <article
      aria-labelledby={role ? `${nameId} ${roleId}` : nameId}
      className={cn(
        'rounded-lg border border-border bg-card p-card',
        'transition-all duration-[var(--so-transition-default)]',
        'hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
        className,
      )}
      style={flashStyle}
    >
      {/* Header: avatar + name + status */}
      <div className="flex items-center gap-2.5">
        <Avatar name={name} size="md" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span id={nameId} className="truncate text-body-sm font-semibold text-foreground">
              {name}
            </span>
            <StatusBadge status={status} />
          </div>
          <span id={roleId} className="text-xs text-text-secondary">{role}</span>
        </div>
      </div>

      {/* Body */}
      <div className="mt-2.5 flex flex-col gap-1 border-t border-border pt-2.5">
        <div className="flex items-center gap-1 text-xs">
          <span className="text-muted-foreground">Dept:</span>
          <span className="text-text-secondary">{department}</span>
          {tier && (
            <span className="ml-auto rounded-md border border-border bg-surface px-1.5 py-0.5 text-micro uppercase tracking-wide text-text-secondary">
              {tier}
            </span>
          )}
        </div>
        {model && (
          <div className="flex items-center gap-1 text-xs">
            <span className="text-muted-foreground">Model:</span>
            <span className="truncate font-mono text-text-secondary">{model}</span>
          </div>
        )}
        {currentTask && (
          <div className="flex items-center gap-1 text-xs">
            <span className="text-muted-foreground">Task:</span>
            <span className="truncate text-text-secondary">{currentTask}</span>
          </div>
        )}
        {timestamp && (
          <div className="mt-0.5 text-right">
            {timestampIso ? (
              <time
                dateTime={timestampIso}
                title={formatDateTime(timestampIso)}
                className="font-mono text-micro text-muted-foreground"
              >
                {timestamp}
              </time>
            ) : (
              <span className="font-mono text-micro text-muted-foreground">{timestamp}</span>
            )}
          </div>
        )}
      </div>
    </article>
  )
}
