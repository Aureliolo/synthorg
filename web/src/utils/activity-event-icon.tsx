import { createElement } from 'react'
import {
  Activity,
  ArrowDownCircle,
  ArrowUpCircle,
  Briefcase,
  CheckCircle2,
  CircleDollarSign,
  Inbox,
  Play,
  Send,
  UserMinus,
  UserPlus,
  Wrench,
  type LucideIcon,
  type LucideProps,
} from 'lucide-react'
import type { ActivityEventType } from '@/api/types/agents'

const ACTIVITY_ICON_MAP: Partial<Record<ActivityEventType, LucideIcon>> = {
  hired: UserPlus,
  fired: UserMinus,
  promoted: ArrowUpCircle,
  demoted: ArrowDownCircle,
  onboarded: Briefcase,
  task_completed: CheckCircle2,
  task_started: Play,
  cost_incurred: CircleDollarSign,
  tool_used: Wrench,
  delegation_sent: Send,
  delegation_received: Inbox,
}

const FALLBACK_ICON: LucideIcon = Activity

export interface ActivityEventIconProps extends LucideProps {
  eventType: string
}

/**
 * Render the Lucide icon for an activity event type.
 *
 * The lookup happens inside this component (not at the call site) so the
 * ``react-x/static-components`` rule sees a stable, top-level component
 * declaration at every JSX usage. Lives in its own file (instead of
 * ``utils/agents.tsx``) so React Fast Refresh stays happy: the
 * ``react-refresh/only-export-components`` rule requires component-only
 * modules.
 */
export function ActivityEventIcon({ eventType, ...rest }: ActivityEventIconProps) {
  return createElement(
    ACTIVITY_ICON_MAP[eventType as ActivityEventType] ?? FALLBACK_ICON,
    rest,
  )
}
