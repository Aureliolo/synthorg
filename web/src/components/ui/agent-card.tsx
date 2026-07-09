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
  /** Human-readable personality preset label (e.g. "Visionary Leader"). */
  personality?: string | undefined
  /** Personality trait words. */
  traits?: readonly string[] | undefined
  /** Capability requirements (e.g. "tools", "vision"). */
  capabilities?: readonly string[] | undefined
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

interface MetaItemData {
  label: string
  value: string
  /** Render the value in a monospace font (model ids). */
  mono?: boolean
  /** Span both grid columns (long values like traits / current task). */
  span?: boolean
  /** Muted qualifier shown after the value (e.g. the model's capability tier). */
  suffix?: string | undefined
}

/**
 * The model row, with the capability tier co-located as a muted suffix. Falls
 * back to a standalone tier row when the model id is absent.
 */
function modelMetaItem(props: AgentCardProps): MetaItemData | null {
  if (props.model) {
    return { label: 'Model', value: props.model, mono: true, suffix: props.tier ?? undefined }
  }
  if (props.tier) return { label: 'Tier', value: props.tier }
  return null
}

/** Assemble the metadata items present for this agent, in display order. */
function buildMetaItems(props: AgentCardProps): MetaItemData[] {
  const items: MetaItemData[] = [{ label: 'Dept', value: props.department }]
  const model = modelMetaItem(props)
  if (model) items.push(model)
  if (props.personality) items.push({ label: 'Personality', value: props.personality })
  if (props.capabilities?.length) {
    items.push({ label: 'Capabilities', value: props.capabilities.join(', ') })
  }
  if (props.traits?.length) {
    items.push({ label: 'Traits', value: props.traits.join(', '), span: true })
  }
  if (props.currentTask) items.push({ label: 'Task', value: props.currentTask, span: true })
  return items
}

function MetaItem({ label, value, mono = false, span = false, suffix }: MetaItemData) {
  return (
    <div className={cn('flex min-w-0 items-baseline gap-1 text-xs', span && 'col-span-2')}>
      <span className="shrink-0 text-muted-foreground">{label}:</span>
      <span className={cn('truncate text-text-secondary', mono && 'font-mono')}>{value}</span>
      {suffix && <span className="shrink-0 text-muted-foreground">· {suffix}</span>}
    </div>
  )
}

function CardFooterTime({
  timestamp,
  timestampIso,
}: {
  timestamp: string
  timestampIso?: string | undefined
}) {
  const label = timestampIso ? (
    <time
      dateTime={timestampIso}
      title={formatDateTime(timestampIso)}
      className="font-mono text-micro text-muted-foreground"
    >
      {timestamp}
    </time>
  ) : (
    <span className="font-mono text-micro text-muted-foreground">{timestamp}</span>
  )
  return <div className="mt-1 text-right">{label}</div>
}

export function AgentCard(props: AgentCardProps) {
  const { name, role, status, timestamp, timestampIso, className, flashStyle } = props
  const nameId = useId()
  const roleId = useId()
  const metaItems = buildMetaItems(props)
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
      <div className="flex items-start gap-2.5">
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

      {/* Body: two-column metadata grid */}
      <div className="mt-2.5 border-t border-border pt-2.5">
        <div className="grid grid-cols-2 gap-x-3 gap-y-1">
          {metaItems.map((item) => (
            <MetaItem key={item.label} {...item} />
          ))}
        </div>
        {timestamp && <CardFooterTime timestamp={timestamp} timestampIso={timestampIso} />}
      </div>
    </article>
  )
}
