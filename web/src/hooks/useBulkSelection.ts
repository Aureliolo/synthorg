import { useCallback, useMemo, useState } from 'react'

/**
 * Hook return value for bulk-selection list pages. Exposes the
 * minimum surface every list page needs to wire a row-level
 * checkbox column plus a header "select all" checkbox plus a
 * ``BulkActionBar`` footer:
 *
 * - ``selectedIds``: ``ReadonlySet`` of currently-selected row ids,
 *   safe to pass directly to row components without copying.
 * - ``toggle(id)``: flip the membership of a single id.
 * - ``toggleAll(ids)``: header-checkbox handler. When the current
 *   selection is empty or partial, selects every visible id; when
 *   every visible id is already selected, clears the selection.
 * - ``clear()``: drop every selection (used by the BulkActionBar's
 *   built-in Clear button and after a successful bulk action).
 * - ``isAllSelected(ids)`` / ``isPartiallySelected(ids)``: tri-state
 *   checkbox indicators for the header. ``isPartiallySelected``
 *   returns true when at least one but not every visible id is
 *   selected -- pair with ``aria-checked="mixed"`` on the header
 *   checkbox to render the indeterminate state for screen readers.
 */
export interface BulkSelectionApi {
  readonly selectedIds: ReadonlySet<string>
  readonly count: number
  toggle: (id: string) => void
  toggleAll: (ids: readonly string[]) => void
  clear: () => void
  isAllSelected: (ids: readonly string[]) => boolean
  isPartiallySelected: (ids: readonly string[]) => boolean
}

/**
 * Reusable bulk-selection state for list / grid pages with row-level
 * checkboxes and a "Select all" header. Replaces the per-page
 * ``Set<string>`` + four callbacks pattern that several pages
 * (Workflows, Projects, Approvals) re-implement, and gives the
 * remaining list pages (Messages, Artifacts, Agents, ...) a
 * consistent contract for bulk-action wiring.
 *
 * The returned ``selectedIds`` is a fresh ``Set`` per state update;
 * components that pass it to memoized children should rely on
 * referential equality of the state slice, not deep equality of the
 * set, for re-render decisions.
 */
export function useBulkSelection(): BulkSelectionApi {
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set())

  const toggle = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  const toggleAll = useCallback((ids: readonly string[]) => {
    // Skip the state update entirely when the visible set is empty
    // (e.g. a filter returned zero rows). Without this guard,
    // ``setSelectedIds`` would still allocate a fresh ``Set`` and
    // trigger a no-op re-render across consumers.
    if (ids.length === 0) return
    setSelectedIds((prev) => {
      // If every visible id is already selected, the header checkbox
      // toggles OFF (clear visible selection). Otherwise, select every
      // visible id while preserving any selections from rows the
      // current filter has hidden (so toggling filters then clicking
      // the header checkbox doesn't silently lose hidden selections).
      const allSelected = ids.every((id) => prev.has(id))
      if (allSelected) {
        const next = new Set(prev)
        for (const id of ids) next.delete(id)
        return next
      }
      const next = new Set(prev)
      for (const id of ids) next.add(id)
      return next
    })
  }, [])

  const clear = useCallback(() => {
    // Skip the state update when nothing is selected; otherwise every
    // ``clear()`` call (e.g. on each route change) would allocate a
    // fresh empty Set and trigger a no-op rerender across consumers.
    setSelectedIds((prev) => (prev.size === 0 ? prev : new Set()))
  }, [])

  const isAllSelected = useCallback(
    (ids: readonly string[]): boolean =>
      ids.length > 0 && ids.every((id) => selectedIds.has(id)),
    [selectedIds],
  )

  const isPartiallySelected = useCallback(
    (ids: readonly string[]): boolean => {
      let any = false
      let all = true
      for (const id of ids) {
        if (selectedIds.has(id)) {
          any = true
        } else {
          all = false
        }
      }
      return any && !all
    },
    [selectedIds],
  )

  return useMemo(
    () => ({
      selectedIds,
      count: selectedIds.size,
      toggle,
      toggleAll,
      clear,
      isAllSelected,
      isPartiallySelected,
    }),
    [selectedIds, toggle, toggleAll, clear, isAllSelected, isPartiallySelected],
  )
}
