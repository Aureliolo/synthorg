import { Children, cloneElement, isValidElement } from 'react'
import type { HTMLAttributes, ReactNode, Ref } from 'react'
import { mergeProps } from '@base-ui/react/merge-props'
import { createLogger } from '@/lib/logger'

const log = createLogger('slot')

/**
 * Local polymorphism primitive used by {@link Button} for its `asChild` prop.
 *
 * Clones its only child and merges the passed props onto it using Base UI's
 * {@link mergeProps}, which handles className concatenation and event-handler
 * chaining with the same semantics as Base UI components. For primitives that
 * come directly from `@base-ui/react/*` (Dialog, AlertDialog, Popover, etc.),
 * prefer their native `render` prop instead of this helper -- `<Slot>` exists
 * only to keep the `<Button asChild>` ergonomic working for consumers that
 * would otherwise need to spread props manually.
 *
 * `Children.only` and `cloneElement` are intentional here -- they are the only
 * way to implement Slot semantics. The helper is tightly scoped to `<Button
 * asChild>`, so the `@eslint-react/no-children-only` and
 * `@eslint-react/no-clone-element` warnings are intentionally suppressed.
 */
export interface SlotProps extends HTMLAttributes<HTMLElement> {
  children?: ReactNode
  ref?: Ref<HTMLElement> | undefined
}

function composeRefs<T>(
  ...refs: Array<Ref<T> | undefined>
): (node: T | null) => void {
  return (node) => {
    for (const ref of refs) {
      if (typeof ref === 'function') {
        ref(node)
      } else if (ref !== undefined && ref !== null) {
        ref.current = node
      }
    }
  }
}

export function Slot({ children, ref, ...slotProps }: SlotProps) {
  // eslint-disable-next-line @eslint-react/no-children-count -- Slot requires exactly one child; count is the only safe pre-check before cloneElement
  if (Children.count(children) !== 1 || !isValidElement<Record<string, unknown>>(children)) {
    log.warn(
      'asChild received a non-element child. Props were not forwarded. ' +
        'Wrap the content in a single React element.',
    )
    return children
  }

  const child = children
  const childProps = child.props
  const merged = mergeProps(slotProps as Record<string, unknown>, childProps)

  // Compose the Slot's own ref with any ref the child element carries so
  // neither is silently dropped when both are provided. React 19+ no longer
  // hides `ref` from `child.props`; we read it explicitly so a forwardRef
  // child does not lose its own ref in favour of the Slot's. If React's
  // ref placement changes in a future major, revisit this composition.
  // `childProps.ref` is `unknown`; a real ref is either a callback or a
  // ref object. Guard the shape before the cast so a malformed value becomes
  // `undefined` rather than a bad ref callback firing on the cloned element.
  const rawRef = childProps['ref']
  const childRef: Ref<HTMLElement> | undefined =
    typeof rawRef === 'function' || (typeof rawRef === 'object' && rawRef !== null)
      ? (rawRef as Ref<HTMLElement>)
      : undefined
  const mergedRef = composeRefs<HTMLElement>(ref, childRef)

  // eslint-disable-next-line @eslint-react/no-clone-element -- required for Slot semantics
  return cloneElement(child, {
    ...merged,
    ref: mergedRef,
  } as Record<string, unknown>)
}
