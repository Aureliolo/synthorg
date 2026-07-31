import type { ComponentType, ReactNode } from 'react'
import { cn } from '@/lib/utils'
import { HealthStatusIcon } from './HealthStatusIcon'
import { STATE_META, type SubsystemState } from './health-popover.utils'

export interface HealthStatusRowProps {
  icon: ComponentType<{ className?: string; 'aria-hidden'?: boolean }>
  label: string
  description: string
  state: SubsystemState
  detail?: string | undefined
  /** Extra classes on the card, e.g. a grid column span. */
  className?: string | undefined
  /**
   * Optional recovery affordance rendered in the card footer.
   *
   * A node rather than a config object because the two kinds differ in element:
   * a retry is a button, a remediation is a link to the page that fixes it, and
   * only the call site knows which. The call site also decides when to pass one,
   * because which states are fixable is per-subsystem knowledge: gating that
   * here on `down` hid every remediation an operator could act on, since an
   * unwired memory backend and an absent backup schedule both read `degraded`.
   */
  action?: ReactNode
}

export function HealthStatusRow({
  icon: Icon,
  label,
  description,
  state,
  detail,
  action,
  className,
}: HealthStatusRowProps) {
  const meta = STATE_META[state]
  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-lg border p-card transition-colors',
        meta.borderClass,
        meta.bgClass,
        className,
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
      {action !== undefined && <div className="mt-1 self-start">{action}</div>}
    </div>
  )
}
