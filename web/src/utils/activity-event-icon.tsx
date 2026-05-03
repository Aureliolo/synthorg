import { createElement } from 'react'
import type { LucideProps } from 'lucide-react'
import { getActivityEventIcon } from '@/utils/agents'

export interface ActivityEventIconProps extends LucideProps {
  eventType: string
}

/**
 * Render the Lucide icon for an activity event type.
 *
 * Delegates the lookup + fallback to ``getActivityEventIcon`` in
 * ``@/utils/agents`` so the ``ACTIVITY_ICON_MAP`` Record stays a single
 * source of truth.
 *
 * The lookup happens inside this component (not at the call site) so the
 * ``react-x/static-components`` rule sees a stable, top-level component
 * declaration at every JSX usage. Lives in its own file (instead of
 * ``utils/agents.ts``) so React Fast Refresh stays happy: the
 * ``react-refresh/only-export-components`` rule requires component-only
 * modules.
 */
export function ActivityEventIcon({ eventType, ...rest }: ActivityEventIconProps) {
  return createElement(getActivityEventIcon(eventType), rest)
}
