import { cn } from '@/lib/utils'

export interface SkeletonProps {
  className?: string | undefined
  /** Whether to show shimmer animation (respects prefers-reduced-motion). Default: true. */
  shimmer?: boolean | undefined
  style?: React.CSSProperties | undefined
}

const SHIMMER_CLASSES =
  'so-shimmer bg-gradient-to-r from-border via-border-bright to-border bg-[length:200%_100%] animate-[so-shimmer_1.5s_ease-in-out_infinite]'

export function Skeleton({ className, shimmer = true, style, ...rest }: SkeletonProps & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'rounded bg-border',
        shimmer && SHIMMER_CLASSES,
        className,
      )}
      style={style}
      {...rest}
    />
  )
}

export interface SkeletonTextProps extends SkeletonProps {
  /** Number of text lines (default: 3). */
  lines?: number
  /** Width of the last line (default: "60%"). */
  lastLineWidth?: string
}

export function SkeletonText({
  lines = 3,
  lastLineWidth = '60%',
  shimmer,
  className,
}: SkeletonTextProps) {
  return (
    <div className={cn('flex flex-col gap-2', className)}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton
          key={i}
          shimmer={shimmer}
          className="h-3 rounded"
          data-skeleton-line=""
          style={i === lines - 1 ? { width: lastLineWidth } : undefined}
        />
      ))}
    </div>
  )
}

export interface SkeletonCardProps extends SkeletonProps {
  /** Show header skeleton. */
  header?: boolean
  /** Number of body lines (default: 3). */
  lines?: number
}

export function SkeletonCard({
  header,
  lines = 3,
  shimmer,
  className,
}: SkeletonCardProps) {
  return (
    <div className={cn('rounded-lg border border-border bg-card p-card space-y-3', className)}>
      {header && (
        <div className="flex items-center gap-3" data-skeleton-header="">
          <Skeleton shimmer={shimmer} className="size-5 rounded" />
          <Skeleton shimmer={shimmer} className="h-4 w-32 rounded" />
        </div>
      )}
      <SkeletonText lines={lines} shimmer={shimmer} />
    </div>
  )
}

export function SkeletonMetric({ shimmer, className }: SkeletonProps) {
  return (
    <div className={cn('rounded-lg border border-border bg-card p-card space-y-3', className)}>
      <Skeleton shimmer={shimmer} className="h-3 w-20 rounded" data-testid="skeleton-label" />
      <Skeleton shimmer={shimmer} className="h-7 w-16 rounded" data-testid="skeleton-value" />
      <Skeleton shimmer={shimmer} className="h-0.5 w-full rounded" data-testid="skeleton-progress" />
    </div>
  )
}

export interface SkeletonChartProps extends SkeletonProps {
  /** Aspect ratio for the chart placeholder. Default: ``2.4`` matches recharts ResponsiveContainer's default. */
  aspectRatio?: number
}

/**
 * Placeholder used as a Suspense fallback for lazy-loaded chart
 * components. Sizes itself via ``aspect-ratio`` so the layout doesn't
 * shift when recharts hydrates and the ResponsiveContainer measures
 * its parent.
 */
export function SkeletonChart({ aspectRatio = 2.4, shimmer, className }: SkeletonChartProps) {
  return (
    <Skeleton
      shimmer={shimmer}
      data-testid="skeleton-chart"
      className={cn('w-full rounded-lg', className)}
      style={{ aspectRatio: String(aspectRatio) }}
    />
  )
}

export interface SkeletonTableProps extends SkeletonProps {
  /** Number of rows (default: 5). */
  rows?: number
  /** Number of columns (default: 4). */
  columns?: number
}

export function SkeletonTable({
  rows = 5,
  columns = 4,
  shimmer,
  className,
}: SkeletonTableProps) {
  return (
    <div className={cn('space-y-2', className)}>
      {Array.from({ length: rows }, (_, rowIdx) => (
        <div
          key={rowIdx}
          data-skeleton-row=""
          className="flex gap-4 rounded-md border border-border bg-card p-card"
        >
          {Array.from({ length: columns }, (_, colIdx) => (
            <Skeleton
              key={colIdx}
              shimmer={shimmer}
              data-skeleton-cell=""
              className={cn('h-3 flex-1 rounded', colIdx === 0 && 'max-w-24')}
            />
          ))}
        </div>
      ))}
    </div>
  )
}
