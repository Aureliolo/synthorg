import { CheckCircle2, Circle, Loader2, XCircle, type LucideIcon } from 'lucide-react'
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

interface StageMeta {
  readonly Icon: LucideIcon
  readonly iconColor: string
  readonly iconExtra: string
  readonly labelColor: string
}

const STAGE_META: Record<ProgressStageStatus, StageMeta> = {
  pending: {
    Icon: Circle,
    iconColor: 'text-muted-foreground',
    iconExtra: '',
    labelColor: 'text-muted-foreground',
  },
  running: {
    Icon: Loader2,
    iconColor: 'text-accent',
    iconExtra: 'animate-spin',
    labelColor: 'text-foreground',
  },
  done: {
    Icon: CheckCircle2,
    iconColor: 'text-success',
    iconExtra: '',
    labelColor: 'text-foreground',
  },
  failed: {
    Icon: XCircle,
    iconColor: 'text-danger',
    iconExtra: '',
    labelColor: 'text-foreground',
  },
}

interface StageRowProps {
  stage: ProgressStage
}

function StageRow({ stage }: StageRowProps) {
  const meta = STAGE_META[stage.status]
  return (
    <li
      className="flex items-start gap-2 text-sm"
      aria-label={`${stage.label}: ${stage.status}`}
    >
      <meta.Icon
        className={cn('mt-0.5 size-4 shrink-0', meta.iconColor, meta.iconExtra)}
        aria-hidden="true"
        strokeWidth="var(--so-stroke-thin)"
      />
      <div className="min-w-0 flex-1">
        <p className={cn('font-medium', meta.labelColor)}>{stage.label}</p>
        {stage.description && (
          <p className="text-xs text-muted-foreground">{stage.description}</p>
        )}
      </div>
    </li>
  )
}

function _isElapsedWarning(
  elapsed: number | null,
  warningAfterSeconds: number | undefined,
): boolean {
  return (
    elapsed !== null &&
    typeof warningAfterSeconds === 'number' &&
    Number.isFinite(warningAfterSeconds) &&
    warningAfterSeconds > 0 &&
    elapsed >= warningAfterSeconds
  )
}

function ElapsedChip({
  elapsed,
  isWarning,
}: {
  elapsed: number
  isWarning: boolean
}) {
  return (
    <span
      className={cn(
        'font-mono text-xs tabular-nums',
        isWarning ? 'text-warning' : 'text-muted-foreground',
      )}
      aria-label={`Elapsed: ${formatElapsed(elapsed)}`}
    >
      {formatElapsed(elapsed)}
    </span>
  )
}

function IndeterminateHeader({
  label,
  description,
  elapsed,
  isWarning,
}: {
  label: string | undefined
  description: string | undefined
  elapsed: number | null
  isWarning: boolean
}) {
  const chip = elapsed !== null ? <ElapsedChip elapsed={elapsed} isWarning={isWarning} /> : null
  if (!label && !chip) return null
  return (
    <div className="flex items-center justify-between gap-3 text-sm">
      {label && <span className="font-medium text-foreground">{label}</span>}
      <div className="flex items-center gap-2">
        {description && <span className="text-xs text-muted-foreground">{description}</span>}
        {chip}
      </div>
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
  const isWarning = _isElapsedWarning(elapsed, warningAfterSeconds)
  const hasHeader = Boolean(label) || elapsed !== null
  return (
    <div className={cn('space-y-1.5', className)}>
      <IndeterminateHeader
        label={label}
        description={description}
        elapsed={elapsed}
        isWarning={isWarning}
      />
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
      {!hasHeader && description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
    </div>
  )
}

function StagesProgress({
  label,
  description,
  stages,
  className,
}: {
  label: string | undefined
  description: string | undefined
  stages: readonly ProgressStage[] | undefined
  className: string | undefined
}) {
  return (
    <div className={cn('space-y-3', className)}>
      {label && <p className="text-sm font-medium text-foreground">{label}</p>}
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      <ol className="space-y-2" role="list">
        {(stages ?? []).map((stage) => (
          <StageRow key={stage.id} stage={stage} />
        ))}
      </ol>
    </div>
  )
}

function DeterminateBar({
  value,
  label,
  description,
  className,
}: {
  value: number | undefined
  label: string | undefined
  description: string | undefined
  className: string | undefined
}) {
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
        <div
          className="h-full bg-accent transition-[width] duration-[var(--so-transition-medium)] ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>
      {description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
    </div>
  )
}

/**
 * Progress indicator for long-running operations.
 *
 * - `determinate`: labelled bar with percentage, ARIA progressbar.
 * - `indeterminate`: shimmer bar for unknown duration (e.g. "Preparing...").
 * - `stages`: ordered list of checkpoints with done / running / pending / failed
 *   states. Use for multi-step pipelines like fine-tuning or setup flows.
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
      <StagesProgress
        label={label}
        description={description}
        stages={stages}
        className={className}
      />
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
  return (
    <DeterminateBar
      value={value}
      label={label}
      description={description}
      className={className}
    />
  )
}
