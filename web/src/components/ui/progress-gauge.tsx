import { cn, getHealthColor, type SemanticColor } from '@/lib/utils'

const STROKE_COLOR_CLASSES: Record<SemanticColor, string> = {
  success: 'stroke-success',
  accent: 'stroke-accent',
  warning: 'stroke-warning',
  danger: 'stroke-danger',
}

const FILL_COLOR_CLASSES: Record<SemanticColor, string> = {
  success: 'bg-success',
  accent: 'bg-accent',
  warning: 'bg-warning',
  danger: 'bg-danger',
}

const SIZE_CONFIG = {
  sm: {
    radius: 32, stroke: 6, valueSize: 'text-sm', labelSize: 'text-micro',
    trackHeight: 'h-1.5', percentSize: 'text-xs', barLabelSize: 'text-micro',
  },
  md: {
    radius: 48, stroke: 6, valueSize: 'text-lg', labelSize: 'text-compact',
    trackHeight: 'h-2', percentSize: 'text-sm', barLabelSize: 'text-compact',
  },
} as const

interface ProgressGaugeProps {
  value: number
  max?: number
  label?: string
  variant?: 'circular' | 'linear'
  size?: 'sm' | 'md'
  className?: string
}

export function ProgressGauge({
  value,
  max = 100,
  label,
  variant = 'circular',
  size = 'md',
  className,
}: ProgressGaugeProps) {
  const safeMax = Number.isFinite(max) ? Math.max(max, 1) : 1
  const safeValue = Number.isFinite(value) ? value : 0
  const clampedValue = Math.max(0, Math.min(safeValue, safeMax))
  const percentage = Math.round((clampedValue / safeMax) * 100)
  const color = getHealthColor(percentage)
  const config = SIZE_CONFIG[size]

  if (variant === 'linear') {
    const percentageText = (
      <span className={cn('font-mono font-semibold text-foreground', config.percentSize)}>
        {percentage}%
      </span>
    )

    return (
      <div
        role="meter"
        aria-valuenow={percentage}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={label ? `${label}: ${percentage}%` : `${percentage}%`}
        className={cn('flex flex-col gap-1', className)}
      >
        {label && (
          <div className="flex items-baseline justify-between">
            <span className={cn('text-muted-foreground', config.barLabelSize)}>
              {label}
            </span>
            {percentageText}
          </div>
        )}
        <div
          data-testid="progress-track"
          className={cn('w-full overflow-hidden rounded-full bg-border', config.trackHeight)}
        >
          <div
            data-testid="progress-fill"
            className={cn(
              'h-full rounded-full transition-all duration-[900ms] ease-in-out',
              FILL_COLOR_CLASSES[color],
            )}
            style={{ width: `${percentage}%` }}
          />
        </div>
        {!label && percentageText}
      </div>
    )
  }

  // Circular variant (default) -- SVG arc geometry for a 180-degree half-circle
  const { radius, stroke } = config
  const svgWidth = (radius + stroke) * 2
  const svgHeight = radius + stroke * 2
  const cx = svgWidth / 2
  const cy = radius + stroke

  const circumference = Math.PI * radius
  const filledLength = (percentage / 100) * circumference
  const dashOffset = circumference - filledLength
  const accessibleLabel = label ? `${label}: ${percentage}%` : `${percentage}%`

  return (
    <div
      role="meter"
      aria-valuenow={percentage}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={accessibleLabel}
      className={cn('inline-flex flex-col items-center', className)}
    >
      <svg
        width={svgWidth}
        height={svgHeight}
        viewBox={`0 0 ${svgWidth} ${svgHeight}`}
        className="overflow-visible"
      >
        {/* SVG <title> provides redundant accessible context: the
            wrapping div already carries role="meter" + aria-label,
            but readers that expose SVG content directly (some
            assistive tech, browser hover tooltips) read this. */}
        <title>{accessibleLabel}</title>
        {/* Track */}
        <path
          d={`M ${cx - radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx + radius} ${cy}`}
          fill="none"
          strokeWidth={stroke}
          className="stroke-border"
          strokeLinecap="round"
        />
        {/* Fill */}
        <path
          d={`M ${cx - radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx + radius} ${cy}`}
          fill="none"
          strokeWidth={stroke}
          className={cn(STROKE_COLOR_CLASSES[color], 'transition-all duration-[900ms] ease-in-out')}
          style={{
            strokeDasharray: circumference,
            strokeDashoffset: dashOffset,
          }}
          strokeLinecap="round"
        />
        {/* Center value */}
        <text
          x={cx}
          y={cy - (size === 'md' ? 12 : 8)}
          textAnchor="middle"
          className={cn('fill-foreground font-mono font-bold', config.valueSize)}
        >
          {percentage}%
        </text>
      </svg>
      {label && (
        <span className={cn('text-muted-foreground', config.labelSize)}>
          {label}
        </span>
      )}
    </div>
  )
}
