import {
  AlertTriangle,
  CheckCircle2,
  CircleHelp,
  Loader2,
  XCircle,
  type LucideIcon,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SubsystemState } from './health-popover.utils'

const STATE_ICON_MAP: Record<SubsystemState, { Icon: LucideIcon; iconClass: string }> = {
  ok: { Icon: CheckCircle2, iconClass: 'text-success' },
  degraded: { Icon: AlertTriangle, iconClass: 'text-warning' },
  down: { Icon: XCircle, iconClass: 'text-danger' },
  loading: { Icon: Loader2, iconClass: 'animate-spin text-muted-foreground' },
  unknown: { Icon: CircleHelp, iconClass: 'text-muted-foreground' },
}

export interface HealthStatusIconProps {
  state: SubsystemState
  className?: string
}

export function HealthStatusIcon({ state, className }: HealthStatusIconProps) {
  const { Icon, iconClass } = STATE_ICON_MAP[state]
  return (
    <Icon
      className={cn('size-5 shrink-0', iconClass, className)}
      aria-hidden="true"
    />
  )
}
