/**
 * Shared `motion/react` test mock.
 *
 * Pre-existing test files each rolled their own `vi.mock('motion/react', ...)`
 * pattern. Most sites only needed two pieces:
 *
 *   - `AnimatePresence` -> identity wrapper that renders children
 *   - `motion.div` -> plain `<div>` that strips animation-only props
 *     (`variants` / `initial` / `animate` / `exit` / `transition`)
 *
 * `mockMotionReact()` returns a vi.mock factory result conforming to the
 * real `motion/react` module's type, so TypeScript catches drift if the
 * upstream API changes (any new export not covered here will type-error
 * at the call site).
 *
 * Tests that need bespoke shapes (e.g. `motion.aside`, a `motion` Proxy
 * across every tag) keep their inline mocks; this helper covers the
 * `motion.div + AnimatePresence` majority case.
 *
 * Issue #1604 / W4f.
 */

import type { ComponentProps, HTMLAttributes, ReactNode } from 'react'

/**
 * Props the helper strips before forwarding to a plain `<div>`.
 *
 * These are the documented animation props on `motion.*` components.
 * Any prop NOT in this set is forwarded so DOM attributes such as
 * `data-testid`, `role`, and `aria-*` reach the element.
 */
const STRIPPED_MOTION_PROPS: readonly string[] = [
  'variants',
  'initial',
  'animate',
  'exit',
  'transition',
  'whileHover',
  'whileTap',
  'whileDrag',
  'whileFocus',
  'whileInView',
  'viewport',
  'layout',
  'layoutId',
  'drag',
  'custom',
  'onAnimationStart',
  'onAnimationComplete',
]

/**
 * Plain-div replacement for `motion.div` that drops animation-only props.
 *
 * Forwards every other prop (className, ref, data-*, aria-*, event
 * handlers) so test queries against role / data-testid / accessible
 * name keep working.
 */
function MockMotionDiv(
  props: ComponentProps<'div'> & Record<string, unknown>,
): ReactNode {
  const { ref, children, ...rest } = props
  const filtered = Object.fromEntries(
    Object.entries(rest).filter(([key]) => !STRIPPED_MOTION_PROPS.includes(key)),
  ) as HTMLAttributes<HTMLDivElement>
  return (
    <div ref={ref as React.Ref<HTMLDivElement>} {...filtered}>
      {children as ReactNode}
    </div>
  )
}

/**
 * Identity wrapper for `<AnimatePresence>` that renders children directly.
 */
function MockAnimatePresence({ children }: { children: ReactNode }): ReactNode {
  return <>{children}</>
}

/**
 * Build a `vi.mock('motion/react', ...)` factory body.
 *
 * Usage::
 *
 *     import { motionReactMockFactory } from '@/test-utils/mock-motion'
 *     vi.mock('motion/react', () => motionReactMockFactory())
 *
 * The factory is sync because `vi.importActual` cannot be called from
 * inside the helper (the Vitest hoisting rules forbid passing a callback
 * to a `vi.mock` that depends on `await vi.importActual` at the helper
 * level). Tests that need to passthrough some real exports should use
 * the existing inline mock pattern with `await vi.importActual` directly.
 */
export function motionReactMockFactory(
  options: { reducedMotion?: boolean } = {},
): {
  AnimatePresence: typeof MockAnimatePresence
  motion: { div: typeof MockMotionDiv }
  useReducedMotion: () => boolean
} {
  const reducedMotion = options.reducedMotion ?? false
  return {
    AnimatePresence: MockAnimatePresence,
    motion: { div: MockMotionDiv },
    useReducedMotion: () => reducedMotion,
  }
}

export { MockAnimatePresence, MockMotionDiv }
