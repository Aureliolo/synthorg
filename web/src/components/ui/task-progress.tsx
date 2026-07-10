/**
 * Live task-execution progress panel.
 *
 * Presentational leaf: renders the accumulated run status + stages produced by
 * `useTaskProgress`. Shown inline in the approval review drawer so an operator
 * watches the spawned task execute (start, tool-call by tool-call, finish/fail)
 * instead of staring at a silent gap until the completion review appears.
 */

import { CheckCircle2, Loader2, PlugZap, XCircle } from 'lucide-react'

import { cn } from '@/lib/utils'
import {
  ProgressIndicator,
  type ProgressStage,
} from '@/components/ui/progress-indicator'

export type TaskProgressStatus =
  | 'running'
  | 'finished'
  | 'error'
  | 'disconnected'

export interface TaskProgressProps {
  /** Current run status. */
  status: TaskProgressStatus
  /** Accumulated progress stages (empty until the first event arrives). */
  stages: readonly ProgressStage[]
  className?: string
}

interface HeaderMeta {
  readonly Icon: typeof Loader2
  readonly iconClass: string
  readonly label: string
}

const HEADER_META: Record<TaskProgressStatus, HeaderMeta> = {
  running: { Icon: Loader2, iconClass: 'text-accent animate-spin', label: 'Working' },
  finished: { Icon: CheckCircle2, iconClass: 'text-success', label: 'Run finished' },
  error: { Icon: XCircle, iconClass: 'text-danger', label: 'Run failed' },
  // Distinct from `error`: the run may still be fine; only live updates stopped.
  disconnected: {
    Icon: PlugZap,
    iconClass: 'text-muted-foreground',
    label: 'Live updates unavailable',
  },
}

export function TaskProgress({ status, stages, className }: TaskProgressProps) {
  const meta = HEADER_META[status]
  return (
    <div
      className={cn(
        'flex flex-col gap-section-gap rounded-lg border border-border bg-surface p-card',
        className,
      )}
      aria-live="polite"
      aria-busy={status === 'running'}
    >
      <div className="flex items-center gap-grid-gap text-sm font-medium">
        <meta.Icon className={cn('size-4 shrink-0', meta.iconClass)} aria-hidden />
        <span>{meta.label}</span>
      </div>
      {stages.length === 0 ? (
        <ProgressIndicator variant="indeterminate" label="Starting run" />
      ) : (
        <ProgressIndicator variant="stages" stages={stages} />
      )}
    </div>
  )
}
