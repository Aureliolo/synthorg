import { cn } from '@/lib/utils'
import { useCountAnimation } from '@/hooks/useCountAnimation'
import { Sparkline } from './sparkline'

export interface MetricCardProps {
  label: string
  value: string | number
  change?: { value: number; direction: 'up' | 'down' }
  sparklineData?: number[]
  progress?: { current: number; total: number }
  subText?: string
  className?: string
  /** Inline style for flash animation (from useFlash). */
  flashStyle?: React.CSSProperties
  /** Whether to animate numeric value transitions. Default: false. */
  animateValue?: boolean
}

function _computeProgressPct(progress: MetricCardProps['progress']): number {
  if (!progress || progress.total <= 0) return 0
  return Math.max(0, Math.min(100, Math.round((progress.current / progress.total) * 100)))
}

function MetricProgressBar({ label, pct }: { label: string; pct: number }) {
  return (
    <div
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`${label} progress`}
      className="mt-2 h-0.5 w-full overflow-hidden rounded-full bg-border"
    >
      <div
        className="h-full rounded-full bg-accent transition-all duration-[900ms]"
        style={{
          width: `${pct}%`,
          transitionTimingFunction: 'cubic-bezier(0.4, 0, 0.2, 1)',
        }}
      />
    </div>
  )
}

function MetricFooter({
  subText,
  change,
}: {
  subText: string | undefined
  change: MetricCardProps['change']
}) {
  if (!subText && !change) return null
  return (
    <div className="mt-2 flex items-center justify-between">
      {subText && <span className="text-xs text-muted-foreground">{subText}</span>}
      {change && <ChangeBadge {...change} className={subText ? undefined : 'ml-auto'} />}
    </div>
  )
}

function MetricHeader({
  label,
  sparklineData,
}: {
  label: string
  sparklineData: number[] | undefined
}) {
  const hasSparkline = sparklineData && sparklineData.length > 1
  return (
    <div className="flex items-start justify-between">
      <span className="text-compact uppercase tracking-[0.06em] text-muted-foreground">
        {label}
      </span>
      {hasSparkline && <Sparkline data={sparklineData} width={60} height={28} />}
    </div>
  )
}

function _resolveDisplayValue(
  value: string | number,
  animateValue: boolean,
  animatedValue: number,
): string | number {
  const numericValue = typeof value === 'number' ? value : undefined
  return animateValue && numericValue !== undefined ? animatedValue : value
}

export function MetricCard({
  label,
  value,
  change,
  sparklineData,
  progress,
  subText,
  className,
  flashStyle,
  animateValue = false,
}: MetricCardProps) {
  const numericValue = typeof value === 'number' ? value : undefined
  const animatedValue = useCountAnimation(numericValue ?? 0)
  const displayValue = _resolveDisplayValue(value, animateValue, animatedValue)
  const progressPct = _computeProgressPct(progress)
  return (
    <div
      className={cn(
        'rounded-lg border border-border bg-card p-card',
        'transition-colors duration-200',
        'hover:bg-card-hover',
        className,
      )}
      style={flashStyle}
    >
      <MetricHeader label={label} sparklineData={sparklineData} />
      <div
        className="mt-1 font-mono text-metric font-bold leading-tight tracking-tight text-foreground"
        data-testid="metric-value"
      >
        {displayValue}
      </div>
      {progress && <MetricProgressBar label={label} pct={progressPct} />}
      <MetricFooter subText={subText} change={change} />
    </div>
  )
}

function ChangeBadge({ value, direction, className }: { value: number; direction: 'up' | 'down'; className?: string }) {
  const isUp = direction === 'up'
  const label = isUp ? `Up ${value} percent` : `Down ${value} percent`

  return (
    <span
      aria-label={label}
      className={cn(
        'inline-flex items-center rounded px-1.5 py-0.5',
        'font-mono text-compact font-medium',
        isUp
          ? 'bg-success/8 text-success border border-success/20'
          : 'bg-danger/8 text-danger border border-danger/20',
        className,
      )}
    >
      {isUp ? '+' : '-'}{value}%
    </span>
  )
}
