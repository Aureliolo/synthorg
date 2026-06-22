import { useId } from 'react'
import { cn } from '@/lib/utils'

export interface SparklineProps {
  data: number[]
  color?: string
  width?: number
  height?: number
  animated?: boolean
  className?: string
  /**
   * Accessible name for the chart. Required for standalone usage (the
   * sparkline becomes a labeled `role="img"` when set).
   *
   * When omitted, the sparkline is treated as decoration and emits
   * `aria-hidden="true"`. Use this mode when the chart is layered
   * beside a labeled sibling (for example, inside a `MetricCard`
   * whose `label` + `value` already carry the semantic meaning).
   */
  ariaLabel?: string
}

function buildPoints(data: number[], width: number, height: number): string {
  const min = Math.min(...data)
  const max = Math.max(...data)
  const range = max - min || 1
  const padding = 2 // leave room for end dot

  return data
    .map((value, i) => {
      const x = padding + (i / (data.length - 1)) * (width - padding * 2)
      const y = height - padding - ((value - min) / range) * (height - padding * 2)
      return `${x},${y}`
    })
    .join(' ')
}

interface SparklineGeometry {
  points: string
  fillPoints: string
  lastX: number
  lastY: number
  approxPathLength: number
}

const SPARKLINE_PADDING = 2

function _computeGeometry(data: number[], width: number, height: number): SparklineGeometry {
  const points = buildPoints(data, width, height)
  const pairs = points.split(' ')
  const lastPair = pairs[pairs.length - 1] ?? ''
  const [rawX = '0', rawY = '0'] = lastPair.split(',')
  return {
    points,
    fillPoints: `${SPARKLINE_PADDING},${height - SPARKLINE_PADDING} ${points} ${width - SPARKLINE_PADDING},${height - SPARKLINE_PADDING}`,
    lastX: parseFloat(rawX),
    lastY: parseFloat(rawY),
    approxPathLength: width * 1.5,
  }
}

function SparklineStyleBlock({
  approxPathLength,
  instanceId,
}: {
  approxPathLength: number
  instanceId: string
}) {
  // ``@keyframes sparkline-draw`` is in the global CSS scope, so two
  // Sparkline instances on the same page with different geometries
  // would step on each other's ``stroke-dashoffset`` start frame and
  // animate from the wrong offset. Suffix the keyframe name with the
  // per-instance ``useId`` token so each Sparkline runs its own
  // keyframe definition. ``sparkline-fade`` has no per-instance state
  // (just opacity), so it stays global -- and so does the
  // ``prefers-reduced-motion`` override (it targets the stable class
  // names, not the keyframe names).
  return (
    <style>{`
      @keyframes sparkline-draw-${instanceId} {
        from { stroke-dashoffset: ${approxPathLength}; }
        to { stroke-dashoffset: 0; }
      }
      @keyframes sparkline-fade {
        from { opacity: 0; }
        to { opacity: 1; }
      }
      @media (prefers-reduced-motion: reduce) {
        .sparkline-line, .sparkline-fill, .sparkline-dot {
          animation: none !important;
        }
      }
    `}</style>
  )
}

function SparklinePaths({
  geo,
  gradientId,
  instanceId,
  color,
  animated,
}: {
  geo: SparklineGeometry
  gradientId: string
  instanceId: string
  color: string
  animated: boolean
}) {
  // Source duration + delay from the design-token layer so every shared
  // primitive shares the same animation timing. ``--so-transition-default``
  // is the 200ms tween (matches `@/lib/motion` ``tweenDefault``) and
  // ``--so-transition-slow`` is the 400ms slow tween (matches
  // ``tweenSlow``); the dot fade lands one slow tween after the line draw
  // and fill fade. ``sparkline-draw-${instanceId}`` keeps each
  // Sparkline's stroke-offset keyframe scoped to its own geometry so
  // multiple instances don't interfere with each other (see
  // ``SparklineStyleBlock``).
  const fillStyle = animated
    ? { animation: 'sparkline-fade var(--so-transition-default) ease-out var(--so-transition-default) both' }
    : undefined
  const lineStyle = animated
    ? {
        strokeDasharray: geo.approxPathLength,
        strokeDashoffset: 0,
        animation: `sparkline-draw-${instanceId} var(--so-transition-default) ease-out var(--so-transition-default) both`,
      }
    : undefined
  const dotStyle = animated
    ? { animation: 'sparkline-fade var(--so-transition-default) ease-out var(--so-transition-slow) both' }
    : undefined
  return (
    <>
      <polygon
        className="sparkline-fill"
        points={geo.fillPoints}
        fill={`url(#${gradientId})`}
        style={fillStyle}
      />
      <polyline
        className="sparkline-line"
        points={geo.points}
        stroke={color}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
        style={lineStyle}
      />
      <circle
        className="sparkline-dot"
        cx={geo.lastX}
        cy={geo.lastY}
        r="2"
        fill={color}
        style={dotStyle}
      />
    </>
  )
}

function _a11yPropsFor(
  ariaLabel: string | undefined,
): { role: 'img'; 'aria-label': string } | { 'aria-hidden': true } {
  if (ariaLabel) return { role: 'img', 'aria-label': ariaLabel }
  return { 'aria-hidden': true }
}

export function Sparkline({
  data,
  color = 'var(--so-accent)',
  width = 64,
  height = 24,
  animated = true,
  className,
  ariaLabel,
}: SparklineProps) {
  const gradientId = useId()
  // ``useId`` includes colons that are invalid in CSS keyframe names;
  // sanitise to the [a-zA-Z0-9_-] subset accepted in <ident-token>s.
  const instanceId = useId().replace(/[^a-zA-Z0-9_-]/g, '-')
  if (data.length <= 1) return null
  const geo = _computeGeometry(data, width, height)
  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      fill="none"
      className={cn('shrink-0', className)}
      {..._a11yPropsFor(ariaLabel)}
    >
      {animated && (
        <SparklineStyleBlock
          approxPathLength={geo.approxPathLength}
          instanceId={instanceId}
        />
      )}
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0" />
        </linearGradient>
      </defs>
      <SparklinePaths
        geo={geo}
        gradientId={gradientId}
        instanceId={instanceId}
        color={color}
        animated={animated}
      />
    </svg>
  )
}
