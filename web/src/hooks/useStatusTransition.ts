import { useMemo } from 'react'
import type { Transition } from 'motion/react'
import { statusColorTransition } from '@/lib/motion'
import { getStatusColor, type AgentRuntimeStatus } from '@/utils/agent-status'

type StatusToken = ReturnType<typeof getStatusColor>

/** CSS variable references for semantic status colors. */
const STATUS_COLOR_MAP: Record<StatusToken, string> = {
  success: 'var(--so-success)',
  accent: 'var(--so-accent)',
  warning: 'var(--so-warning)',
  danger: 'var(--so-danger)',
  'text-secondary': 'var(--so-text-secondary)',
}

interface UseStatusTransitionReturn {
  /** The semantic color token name (e.g. "success", "danger", "text-secondary"). */
  displayColor: StatusToken
  /** Props to spread on a motion element for animated color transitions. */
  motionProps: {
    animate: { backgroundColor: string }
    transition: Transition
  }
}

/**
 * Animate between status colors when an agent's runtime status changes.
 *
 * Returns the resolved color and Motion props for smooth transitions.
 */
export function useStatusTransition(
  status: AgentRuntimeStatus,
): UseStatusTransitionReturn {
  const colorToken = getStatusColor(status)
  const cssColor = STATUS_COLOR_MAP[colorToken]

  const motionProps = useMemo(
    () => ({
      animate: { backgroundColor: cssColor },
      transition: statusColorTransition,
    }),
    [cssColor],
  )

  return { displayColor: colorToken, motionProps }
}
