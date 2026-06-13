import type { ComponentType } from 'react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { HealthStatusIcon } from './HealthStatusIcon'
import { STATE_META, type SubsystemState } from './health-popover.utils'

export interface HealthStatusRowProps {
  icon: ComponentType<{ className?: string; 'aria-hidden'?: boolean }>
  label: string
  description: string
  state: SubsystemState
  detail?: string | undefined
  /**
   * Optional recovery action (e.g. "Retry now"). Rendered as a small
   * button inside the card footer; only surfaced when the subsystem is
   * in a terminal failure state.
   */
  action?: { label: string; onClick: () => void } | undefined
}

export function HealthStatusRow({
  icon: Icon,
  label,
  description,
  state,
  detail,
  action,
}: HealthStatusRowProps) {
  const meta = STATE_META[state]
  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-lg border p-card transition-colors',
        meta.borderClass,
        meta.bgClass,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <Icon className="size-4 text-muted-foreground" aria-hidden={true} />
          <span className="text-sm font-semibold text-foreground">{label}</span>
        </div>
        <HealthStatusIcon state={state} />
      </div>
      <p className="text-compact text-muted-foreground">{description}</p>
      <div className="mt-auto flex items-baseline justify-between gap-2 pt-1">
        <span className={cn('text-sm font-semibold', meta.textClass)}>{meta.label}</span>
        {detail && (
          <span className="text-compact text-muted-foreground">{detail}</span>
        )}
      </div>
      {action && state === 'down' && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={action.onClick}
          className="mt-1 self-start"
        >
          {action.label}
        </Button>
      )}
    </div>
  )
}
