import { useCallback, useMemo, useState } from 'react'

/**
 * Row selection for a list that can delete a selection.
 *
 * Selection is held against the rows currently on screen: a row that filtering
 * or a page change has taken away is dropped from the count, so the operator is
 * never told they are deleting something they cannot see. The full set is kept
 * so returning to a page restores what was ticked there.
 */
export interface BulkSelection {
  /** The selected rows that are currently visible. */
  readonly visibleSelected: ReadonlySet<string>
  readonly selectedCount: number
  readonly toggle: (id: string) => void
  readonly clear: () => void
  readonly confirmOpen: boolean
  readonly openConfirm: () => void
  readonly closeConfirm: () => void
  readonly deleting: boolean
  /** Run the delete, then close the dialog and clear what it removed. */
  readonly runDelete: () => Promise<void>
}

export function useBulkSelection(
  visibleIds: readonly string[],
  onDelete: (ids: readonly string[]) => Promise<unknown>,
): BulkSelection {
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set())
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)

  const toggle = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const clear = useCallback(() => setSelectedIds(new Set()), [])

  const visible = useMemo(() => new Set(visibleIds), [visibleIds])
  const visibleSelected = useMemo(() => {
    const next = new Set<string>()
    for (const id of selectedIds) {
      if (visible.has(id)) next.add(id)
    }
    return next
  }, [selectedIds, visible])

  const runDelete = useCallback(async () => {
    setDeleting(true)
    try {
      // The store owns the success / partial / error toast, so the outcome is
      // deliberately discarded here: this hook drives the dialog and selection.
      await onDelete([...visibleSelected])
    } finally {
      // In a finally because the dialog refuses to close while a delete is in
      // flight: a throw that left this flag set would leave the operator
      // holding a modal over a destructive action with no way out of it.
      setDeleting(false)
      setConfirmOpen(false)
      clear()
    }
  }, [visibleSelected, onDelete, clear])

  return {
    visibleSelected,
    selectedCount: visibleSelected.size,
    toggle,
    clear,
    confirmOpen,
    openConfirm: useCallback(() => setConfirmOpen(true), []),
    closeConfirm: useCallback(() => setConfirmOpen(false), []),
    deleting,
    runDelete,
  }
}
