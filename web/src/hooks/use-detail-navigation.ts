import { useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router'

export interface UseDetailNavigationOptions<T> {
  /** Items in the order the user sees them in the parent list. */
  items: readonly T[]
  /** Currently-active id to position the cursor at. */
  currentId: string | undefined | null
  /** Read the id from a list item; defaults to ``item.id``. */
  getId?: (item: T) => string
  /** Build the detail route for a list item. */
  routeFor: (item: T) => string
}

export interface UseDetailNavigationResult {
  canPrev: boolean
  canNext: boolean
  goPrev: (() => void) | null
  goNext: (() => void) | null
  /** ``null`` when ``currentId`` is not in ``items`` (deep link / refresh). */
  position: { current: number; total: number } | null
}

/**
 * Drive a Previous / Next nav bar on a detail page from its parent
 * list. Walks the list in the same order the user sees it (filters
 * + sort applied upstream), so the user keeps their context across
 * navigation. Returns ``position: null`` when the current id is not
 * in the parent list; the consumer should hide the nav bar in that
 * case (deep link / refresh / shared URL).
 */
export function useDetailNavigation<T extends { id: string }>(
  options: UseDetailNavigationOptions<T>,
): UseDetailNavigationResult {
  const { items, currentId, getId = (item) => item.id, routeFor } = options
  const navigate = useNavigate()

  return useMemo(() => {
    if (!currentId) {
      return { canPrev: false, canNext: false, goPrev: null, goNext: null, position: null }
    }
    const idx = items.findIndex((item) => getId(item) === currentId)
    if (idx === -1) {
      return { canPrev: false, canNext: false, goPrev: null, goNext: null, position: null }
    }
    const prev = idx > 0 ? items[idx - 1] : null
    const next = idx < items.length - 1 ? items[idx + 1] : null
    return {
      canPrev: prev !== null,
      canNext: next !== null,
      goPrev: prev ? () => navigate(routeFor(prev)) : null,
      goNext: next ? () => navigate(routeFor(next)) : null,
      position: { current: idx + 1, total: items.length },
    }
  }, [items, currentId, getId, routeFor, navigate])
}

/**
 * Wrapper that always returns the nav-bar handlers as no-op-when-null
 * functions instead of nullable handles. Useful when integrating with
 * APIs that require ``() => void`` rather than ``() => void | null``.
 */
export function useDetailNavigationCallbacks(
  result: UseDetailNavigationResult,
): { goPrev: () => void; goNext: () => void } {
  const { goPrev: goPrevRaw, goNext: goNextRaw } = result
  const goPrev = useCallback(() => {
    if (goPrevRaw) goPrevRaw()
  }, [goPrevRaw])
  const goNext = useCallback(() => {
    if (goNextRaw) goNextRaw()
  }, [goNextRaw])
  return { goPrev, goNext }
}
