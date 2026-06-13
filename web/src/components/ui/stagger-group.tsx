import { useMemo } from 'react'
import { motion } from 'motion/react'
import { cardEntrance } from '@/lib/motion'

export interface StaggerGroupProps {
  children?: React.ReactNode
  className?: string
  /** Stagger delay between children in seconds (default: 0.03). */
  staggerDelay?: number
  /** Whether to animate on mount (default: true). */
  animate?: boolean
  /** ARIA role (e.g. "list"). When set, children should carry `role="listitem"`. */
  role?: string
  /** Accessible name for the group when `role` is set. */
  'aria-label'?: string
  /** ID of an element that labels the group. */
  'aria-labelledby'?: string
}

export interface StaggerItemProps {
  children: React.ReactNode
  className?: string
  /** Motion layoutId for reorder animations. */
  layoutId?: string
  /** Enable layout animation for smooth size changes. */
  layout?: boolean
  'data-testid'?: string
  /** ARIA role (e.g. "listitem"). */
  role?: string
}

/**
 * Container that staggers its children's entrance animations.
 *
 * Use with `StaggerItem` children for coordinated card entrance effects.
 * Consider limiting to ~10 items (300ms total at default stagger) to avoid long entrance sequences.
 */
export function StaggerGroup({
  children,
  className,
  staggerDelay = 0.03,
  animate = true,
  role,
  'aria-label': ariaLabel,
  'aria-labelledby': ariaLabelledBy,
}: StaggerGroupProps) {
  const containerVariants = useMemo(
    () => ({
      hidden: {},
      visible: {
        transition: {
          staggerChildren: staggerDelay,
          delayChildren: 0,
        },
      },
    }),
    [staggerDelay],
  )

  return (
    <motion.div
      variants={containerVariants}
      initial={animate ? 'hidden' : false}
      animate={animate ? 'visible' : false}
      className={className}
      role={role}
      aria-label={ariaLabel}
      aria-labelledby={ariaLabelledBy}
    >
      {children}
    </motion.div>
  )
}

/**
 * Individual item within a StaggerGroup.
 *
 * Applies the cardEntrance animation variant (fade up from 8px below).
 * Supports Motion layout animations for smooth reordering.
 */
export function StaggerItem({
  children,
  className,
  layoutId,
  layout,
  'data-testid': testId,
  role,
}: StaggerItemProps) {
  // exactOptionalPropertyTypes: Motion's HTMLMotionProps decline an explicit
  // `undefined`, so optional props are spread in only when defined.
  const optionalProps = {
    ...(layoutId !== undefined && { layoutId }),
    ...(layout !== undefined && { layout }),
    ...(className !== undefined && { className }),
    ...(testId !== undefined && { 'data-testid': testId }),
    ...(role !== undefined && { role }),
  }
  return (
    <motion.div variants={cardEntrance} {...optionalProps}>
      {children}
    </motion.div>
  )
}
