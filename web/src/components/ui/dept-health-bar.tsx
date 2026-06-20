import { cn } from '@/lib/utils'
import { getHealthColor, type SemanticColor } from '@/utils/agent-status'

const BAR_COLOR_CLASSES: Record<SemanticColor, string> = {
  success: 'bg-success',
  accent: 'bg-accent',
  warning: 'bg-warning',
  danger: 'bg-danger',
}

export interface DeptHealthBarProps {
  name: string
  health?: number | null
  agentCount: number
  className?: string
}

function _clampHealth(health: number | null | undefined): number | null {
  if (health == null) return null
  return Math.max(0, Math.min(health, 100))
}

function HealthBarMeter({ name, clamped }: { name: string; clamped: number | null }) {
  const label = `${name} utilization: ${clamped != null ? `${clamped}%` : 'unavailable'}`
  const meterProps = clamped != null
    ? { role: 'meter' as const, 'aria-valuenow': clamped, 'aria-valuemin': 0, 'aria-valuemax': 100 }
    : {}
  const color = clamped != null ? getHealthColor(clamped) : null
  return (
    <div
      {...meterProps}
      aria-label={label}
      className="h-1.5 w-full overflow-hidden rounded-full bg-border"
    >
      {clamped != null && color != null && (
        <div
          className={cn(
            'h-full rounded-full transition-all duration-[var(--so-transition-progress)]',
            BAR_COLOR_CLASSES[color],
          )}
          style={{
            width: `${clamped}%`,
            transitionTimingFunction: 'cubic-bezier(0.4, 0, 0.2, 1)',
          }}
        />
      )}
    </div>
  )
}

export function DeptHealthBar({
  name,
  health,
  agentCount,
  className,
}: DeptHealthBarProps) {
  const clamped = _clampHealth(health)
  const agentLabel = agentCount === 1 ? 'agent' : 'agents'
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium text-foreground">{name}</span>
        <span className="font-mono text-xs font-semibold text-foreground">
          {clamped != null ? `${clamped}%` : 'N/A'}
        </span>
      </div>
      <HealthBarMeter name={name} clamped={clamped} />
      <div className="flex gap-3 text-xs text-muted-foreground">
        <span>{agentCount} {agentLabel}</span>
      </div>
    </div>
  )
}
