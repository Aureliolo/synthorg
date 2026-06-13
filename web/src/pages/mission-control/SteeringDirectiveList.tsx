import { Compass } from 'lucide-react'

import type { ActiveSteeringDirective, InterventionKind } from '@/api/types'
import { EmptyState } from '@/components/ui/empty-state'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/utils/format'

function DirectiveKindBadge({ kind }: { kind: InterventionKind }) {
  const isRedirect = kind === 'redirect'
  return (
    <span
      className={cn(
        'rounded-full px-2 py-0.5 text-xs font-medium uppercase',
        isRedirect ? 'bg-warning/10 text-warning' : 'bg-accent/10 text-accent',
      )}
    >
      {kind}
    </span>
  )
}

function NarrowingLine({ directive }: { directive: ActiveSteeringDirective }) {
  const parts: string[] = []
  if (directive.narrow_task_ids.length > 0) {
    parts.push(`${directive.narrow_task_ids.length} task(s)`)
  }
  if (directive.narrow_agent_ids.length > 0) {
    parts.push(`${directive.narrow_agent_ids.length} agent(s)`)
  }
  if (parts.length === 0) return null
  return (
    <p className="text-xs text-text-secondary">Narrowed to {parts.join(', ')}</p>
  )
}

function DirectiveCard({ directive }: { directive: ActiveSteeringDirective }) {
  return (
    <div className="rounded-lg border border-border bg-card p-card">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <DirectiveKindBadge kind={directive.kind} />
        <span className="text-xs text-text-secondary">
          {directive.author} · {formatRelativeTime(directive.recorded_at)}
        </span>
      </div>
      <p className="mt-2 text-sm text-foreground">{directive.text}</p>
      <NarrowingLine directive={directive} />
    </div>
  )
}

export interface SteeringDirectiveListProps {
  directives: readonly ActiveSteeringDirective[]
  loading: boolean
}

export function SteeringDirectiveList({
  directives,
  loading,
}: SteeringDirectiveListProps) {
  if (loading && directives.length === 0) {
    return (
      <div className="space-y-grid-gap">
        <Skeleton className="h-20 w-full" />
        <Skeleton className="h-20 w-full" />
      </div>
    )
  }
  if (directives.length === 0) {
    return (
      <EmptyState
        icon={Compass}
        title="No active directives"
        description="Issue a hint or redirect above to steer this project's in-flight agents."
      />
    )
  }
  return (
    <div className="space-y-grid-gap">
      {directives.map((directive) => (
        <DirectiveCard key={directive.entry_id} directive={directive} />
      ))}
    </div>
  )
}
