import type { Variants } from 'motion/react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { reducedPageVariants, tweenCrossfadeEnter, tweenCrossfadeExit } from '@/lib/motion'
import { cn } from '@/lib/utils'

export interface AnimatedPresenceProps {
  children: React.ReactNode
  /** Unique key for AnimatePresence tracking (typically location.pathname). */
  routeKey: string
  className?: string
}

/**
 * Page transition variants.
 *
 * A pure opacity crossfade with no horizontal translation: on dense
 * dashboard layouts (and under React StrictMode's dev double-mount) any
 * slide reads as a layout shift rather than a transition. Duration is kept
 * under 120ms total so the change is imperceptible for everyday navigation
 * but still visible as a soft fade rather than a hard swap.
 */
const pageVariants: Variants = {
  initial: { opacity: 0 },
  animate: {
    opacity: 1,
    transition: tweenCrossfadeEnter,
  },
  exit: {
    opacity: 0,
    transition: tweenCrossfadeExit,
  },
}

/**
 * Page transition wrapper using Motion's AnimatePresence.
 *
 * Wraps children with enter/exit animations keyed by `routeKey`.
 * Automatically falls back to reduced-motion variants when the user
 * has `prefers-reduced-motion: reduce` enabled.
 */
export function AnimatedPresence({
  children,
  routeKey,
  className,
}: AnimatedPresenceProps) {
  const shouldReduceMotion = useReducedMotion()
  const variants = shouldReduceMotion ? reducedPageVariants : pageVariants

  return (
    // mode="popLayout" lets the outgoing page exit while the incoming
    // page enters in the same frame -- no gap, no "blank for a tick"
    // between routes (mode="wait" would force the exit to finish before
    // the new page mounts, leaving a perceptible blank gap).
    //
    // `initial={false}` skips the enter animation on the first mount
    // of AnimatePresence itself (the very first page load, and every
    // StrictMode-induced remount in dev).  Without this, every fresh
    // app load would play a 120 ms fade-in on the landing page that
    // the user did not trigger; in dev StrictMode remounts it twice
    // which reads as a "flash back and forth" before the page
    // settles.  Subsequent in-app navigation still plays the full
    // crossfade because that swaps the child key, not the boundary.
    //
    // `h-full` (not `flex-1`) on the motion.div is load-bearing: this
    // wrapper lives inside `<main>` which is a block element, so
    // `flex-1` has no effect and leaves the motion.div with `height:
    // auto` (content-based).  Any descendant that relies on `h-full`
    // (e.g. the Org Chart's React Flow container, which needs a
    // concrete parent height to render at all) then collapses to zero.
    // `h-full` resolves against `<main>`'s own concrete height (it is
    // a flex-1 child of a `h-screen` column) and propagates the
    // available height down to pages that want to fill it; pages that
    // are shorter than the viewport still scroll naturally because
    // `<main>` owns the `overflow-y-auto` above this wrapper.
    <AnimatePresence mode="popLayout" initial={false}>
      <motion.div
        key={routeKey}
        variants={variants}
        initial="initial"
        animate="animate"
        exit="exit"
        className={cn('h-full', className)}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  )
}
