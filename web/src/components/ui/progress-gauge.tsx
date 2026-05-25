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

interface GaugeMath {
  percentage: number
  color: ReturnType<typeof getHealthColor>
}

function _gaugeMath(value: number, max: number): GaugeMath {
  const safeMax = Number.isFinite(max) ? Math.max(max, 1) : 1
  const safeValue = Number.isFinite(value) ? value : 0
  const clampedValue = Math.max(0, Math.min(safeValue, safeMax))
  const percentage = Math.round((clampedValue / safeMax) * 100)
  return { percentage, color: getHealthColor(percentage) }
}

function LinearGauge({
  percentage,
  color,
  config,
  label,
  className,
}: {
  percentage: number
  color: ReturnType<typeof getHealthColor>
  config: typeof SIZE_CONFIG[keyof typeof SIZE_CONFIG]
  label: string | undefined
  className: string | undefined
}) {
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
          <span className={cn('text-muted-foreground', config.barLabelSize)}>{label}</span>
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
            'h-full rounded-full transition-all duration-[var(--so-transition-progress)] ease-in-out',
            FILL_COLOR_CLASSES[color],
          )}
          style={{ width: `${percentage}%` }}
        />
      </div>
      {!label && percentageText}
    </div>
  )
}

interface CircularGeometry {
  svgWidth: number
  svgHeight: number
  cx: number
  cy: number
  arcPath: string
  circumference: number
  dashOffset: number
}

function _circularGeometry(
  radius: number,
  stroke: number,
  percentage: number,
): CircularGeometry {
  const svgWidth = (radius + stroke) * 2
  const svgHeight = radius + stroke * 2
  const cx = svgWidth / 2
  const cy = radius + stroke
  const circumference = Math.PI * radius
  return {
    svgWidth, svgHeight, cx, cy, circumference,
    arcPath: `M ${cx - radius} ${cy} A ${radius} ${radius} 0 0 1 ${cx + radius} ${cy}`,
    dashOffset: circumference - (percentage / 100) * circumference,
  }
}

function CircularGauge({
  percentage,
  color,
  config,
  size,
  label,
  className,
}: {
  percentage: number
  color: ReturnType<typeof getHealthColor>
  config: typeof SIZE_CONFIG[keyof typeof SIZE_CONFIG]
  size: 'sm' | 'md'
  label: string | undefined
  className: string | undefined
}) {
  const geo = _circularGeometry(config.radius, config.stroke, percentage)
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
        width={geo.svgWidth}
        height={geo.svgHeight}
        viewBox={`0 0 ${geo.svgWidth} ${geo.svgHeight}`}
        className="overflow-visible"
      >
        {/* SVG <title> provides redundant accessible context: the
            wrapping div already carries role="meter" + aria-label,
            but readers that expose SVG content directly (some
            assistive tech, browser hover tooltips) read this. */}
        <title>{accessibleLabel}</title>
        <path
          d={geo.arcPath}
          fill="none"
          strokeWidth={config.stroke}
          className="stroke-border"
          strokeLinecap="round"
        />
        <path
          d={geo.arcPath}
          fill="none"
          strokeWidth={config.stroke}
          className={cn(STROKE_COLOR_CLASSES[color], 'transition-all duration-[var(--so-transition-progress)] ease-in-out')}
          style={{
            strokeDasharray: geo.circumference,
            strokeDashoffset: geo.dashOffset,
          }}
          strokeLinecap="round"
        />
        <text
          x={geo.cx}
          y={geo.cy - (size === 'md' ? 12 : 8)}
          textAnchor="middle"
          className={cn('fill-foreground font-mono font-bold', config.valueSize)}
        >
          {percentage}%
        </text>
      </svg>
      {label && (
        <span className={cn('text-muted-foreground', config.labelSize)}>{label}</span>
      )}
    </div>
  )
}

export function ProgressGauge({
  value,
  max = 100,
  label,
  variant = 'circular',
  size = 'md',
  className,
}: ProgressGaugeProps) {
  const { percentage, color } = _gaugeMath(value, max)
  const config = SIZE_CONFIG[size]
  if (variant === 'linear') {
    return (
      <LinearGauge
        percentage={percentage}
        color={color}
        config={config}
        label={label}
        className={className}
      />
    )
  }
  return (
    <CircularGauge
      percentage={percentage}
      color={color}
      config={config}
      size={size}
      label={label}
      className={className}
    />
  )
}
