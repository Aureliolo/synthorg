import { cn } from '@/lib/utils'
import { formatNumber } from '@/utils/format'

export interface TokenSegment {
  label: string
  value: number
  color?: string
}

export interface TokenUsageBarProps {
  segments: readonly TokenSegment[]
  total: number
  className?: string
}

const SEGMENT_COLORS = [
  'bg-accent',
  'bg-success',
  'bg-warning',
  'bg-danger',
  'bg-accent-dim',
] as const

export function TokenUsageBar({ segments, total, className }: TokenUsageBarProps) {
  const usedTokens = segments.reduce((sum, s) => sum + s.value, 0)
  const usedPercent = total > 0 ? Math.min(100, (usedTokens / total) * 100) : 0
  const visible = usedTokens > 0 ? segments.filter((s) => s.value > 0) : []
  const clampedMax = Math.max(total, 0)
  const clampedNow = Math.min(Math.max(usedTokens, 0), clampedMax)

  return (
    <div
      role="meter"
      aria-valuenow={clampedNow}
      aria-valuemin={0}
      aria-valuemax={clampedMax}
      aria-valuetext={`${formatNumber(usedTokens)} of ${formatNumber(total)}`}
      aria-label="Token usage"
      className={cn('flex flex-col gap-1', className)}
    >
      <div className="h-2 w-full overflow-hidden rounded-full bg-border">
        <div className="flex h-full" style={{ width: `${usedPercent}%` }}>
          {visible.map((segment, i) => (
            <div
              key={segment.label}
              className={cn(
                'h-full transition-all duration-[var(--so-transition-progress)]',
                segment.color ?? SEGMENT_COLORS[i % SEGMENT_COLORS.length],
                i === 0 && 'rounded-l-full',
                i === visible.length - 1 && 'rounded-r-full',
              )}
              style={{
                width: `${(segment.value / usedTokens) * 100}%`,
                transitionTimingFunction: 'cubic-bezier(0.4, 0, 0.2, 1)',
              }}
              title={`${segment.label}: ${formatNumber(segment.value)} tokens`}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
