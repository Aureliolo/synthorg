import { CheckCircle2, Circle, Loader2, XCircle } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useElapsedSeconds } from '@/hooks/useElapsedSeconds'
import { formatElapsed } from '@/utils/format'

export type ProgressStageStatus = 'pending' | 'running' | 'done' | 'failed'

export interface ProgressStage {
  id: string
  label: string
  status: ProgressStageStatus
  /** Optional secondary line (e.g. "Step 2 of 5" or elapsed time). */
  description?: string
}

export interface ProgressIndicatorProps {
  /** Visual variant. */
  variant: 'determinate' | 'indeterminate' | 'stages'
  /** [0, 100] for `determinate`. Ignored otherwise. */
  value?: number
  /** Label shown above the bar/list (e.g. "Training model"). */
  label?: string
  /** Optional ETA or status line for determinate/indeterminate variants. */
  description?: string
  /** List of stages for `stages` variant. */
  stages?: readonly ProgressStage[]
  /**
   * When provided on the `indeterminate` variant, renders a live
   * elapsed-time chip ("2m 34s") next to the description and updates
   * once per second. Ignored on the determinate and stages variants.
   */
  startedAt?: Date | string | null
  /**
   * When `startedAt` is set and elapsed exceeds this threshold, the
   * indeterminate bar + elapsed chip switch to a warning colour to
   * signal a long-running operation. Operators interpret this as
   * "this is taking longer than expected"; the run is not interrupted.
   */
  warningAfterSeconds?: number
  className?: string
}

/**
 * Progress indicator for long-running operations.
 *
 * - `determinate`: labeled bar with percentage, ARIA progressbar.
 * - `indeterminate`: shimmer bar for unknown duration (e.g. "Preparing...").
 * - `stages`: ordered list of checkpoints with done / running / pending / failed
 *   states -- use for multi-step pipelines like fine-tuning or setup flows.
 */
export function ProgressIndicator({
  variant,
  value,
  label,
  description,
  stages,
  startedAt,
  warningAfterSeconds,
  className,
}: ProgressIndicatorProps) {
  if (variant === 'stages') {
    return (
      <div className={cn('space-y-3', className)}>
        {label && (
          <p className="text-sm font-medium text-foreground">{label}</p>
        )}
        {description && (
          <p className="text-xs text-muted-foreground">{description}</p>
        )}
        <ol className="space-y-2" role="list">
          {(stages ?? []).map((stage) => (
            <StageRow key={stage.id} stage={stage} />
          ))}
        </ol>
      </div>
    )
  }

  if (variant === 'indeterminate') {
    return (
      <IndeterminateBar
        label={label}
        description={description}
        startedAt={startedAt}
        warningAfterSeconds={warningAfterSeconds}
        className={className}
      />
    )
  }

  // determinate
  const pct = Math.min(100, Math.max(0, Math.round(value ?? 0)))
  return (
    <div className={cn('space-y-1.5', className)}>
      {label && (
        <div className="flex items-center justify-between gap-3 text-sm">
          <span className="font-medium text-foreground">{label}</span>
          <span className="font-mono text-xs text-muted-foreground tabular-nums">{pct}%</span>
        </div>
      )}
      <div
        role="progressbar"
        aria-label={label ?? 'Progress'}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        className="h-1.5 w-full overflow-hidden rounded-full bg-card"
      >
        <div className="h-full bg-accent transition-[width] duration-[var(--so-transition-medium)] ease-out" style={{ width: `${pct}%` }} />
      </div>
      {description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
    </div>
  )
}

interface IndeterminateBarProps {
  label?: string
  description?: string
  startedAt?: Date | string | null
  warningAfterSeconds?: number
  className?: string
}

function IndeterminateBar({
  label,
  description,
  startedAt,
  warningAfterSeconds,
  className,
}: IndeterminateBarProps) {
  const elapsed = useElapsedSeconds(startedAt ?? null)
  const isWarning =
    elapsed !== null &&
    typeof warningAfterSeconds === 'number' &&
    Number.isFinite(warningAfterSeconds) &&
    elapsed >= warningAfterSeconds

  const elapsedChip =
    elapsed !== null ? (
      <span
        className={cn(
          'font-mono text-xs tabular-nums',
          isWarning ? 'text-warning' : 'text-muted-foreground',
        )}
        aria-label={`Elapsed: ${formatElapsed(elapsed)}`}
      >
        {formatElapsed(elapsed)}
      </span>
    ) : null

  return (
    <div className={cn('space-y-1.5', className)}>
      {(label || elapsedChip) && (
        <div className="flex items-center justify-between gap-3 text-sm">
          {label && <span className="font-medium text-foreground">{label}</span>}
          <div className="flex items-center gap-2">
            {description && <span className="text-xs text-muted-foreground">{description}</span>}
            {elapsedChip}
          </div>
        </div>
      )}
      <div
        role="progressbar"
        aria-label={label ?? 'Loading'}
        aria-busy="true"
        className={cn(
          'relative h-1.5 w-full overflow-hidden rounded-full bg-card',
          isWarning && 'ring-1 ring-warning/40',
        )}
      >
        <div
          className={cn(
            'absolute inset-y-0 left-0 w-1/3 animate-[so-indeterminate_var(--so-transition-indeterminate)_ease-in-out_infinite]',
            isWarning ? 'bg-warning' : 'bg-accent',
          )}
        />
      </div>
      {!label && !elapsedChip && description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
    </div>
  )
}

interface StageRowProps {
  stage: ProgressStage
}

function StageRow({ stage }: StageRowProps) {
  const Icon =
    stage.status === 'done' ? CheckCircle2
      : stage.status === 'failed' ? XCircle
      : stage.status === 'running' ? Loader2
      : Circle

  const iconColor =
    stage.status === 'done' ? 'text-success'
      : stage.status === 'failed' ? 'text-danger'
      : stage.status === 'running' ? 'text-accent'
      : 'text-muted-foreground'

  const iconExtra = stage.status === 'running' ? 'animate-spin' : ''

  const labelColor = stage.status === 'pending' ? 'text-muted-foreground' : 'text-foreground'

  return (
    <li className="flex items-start gap-2 text-sm" aria-label={`${stage.label}: ${stage.status}`}>
      <Icon className={cn('mt-0.5 size-4 shrink-0', iconColor, iconExtra)} aria-hidden="true" strokeWidth="var(--so-stroke-thin)" />
      <div className="min-w-0 flex-1">
        <p className={cn('font-medium', labelColor)}>{stage.label}</p>
        {stage.description && (
          <p className="text-xs text-muted-foreground">{stage.description}</p>
        )}
      </div>
    </li>
  )
}
