import { useCallback, useRef, useState } from 'react'

interface DrawerDeleteState {
  deleteOpen: boolean
  setDeleteOpen: (open: boolean) => void
  deleting: boolean
  setDeleting: (value: boolean) => void
  handleDelete: () => Promise<void>
}

/**
 * Shared delete-confirmation state for the org-edit drawers. Owns the
 * confirm-dialog open flag plus the in-flight guard, and runs the delete
 * inside a `finally` so an unexpected reject never strands the dialog in
 * its loading state.
 */
export function useDrawerDelete(
  entityName: string | null | undefined,
  onDelete: (name: string) => Promise<boolean>,
  onClose: () => void,
): DrawerDeleteState {
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const deletingRef = useRef(false)

  // Reset the confirm dialog and spinner when the drawer switches to a
  // different entity, so a stale open dialog or spinner never leaks
  // across targets (react.dev "Adjusting some state when a prop changes").
  const [prevEntityName, setPrevEntityName] = useState(entityName)
  if (entityName !== prevEntityName) {
    setPrevEntityName(entityName)
    setDeleteOpen(false)
    setDeleting(false)
  }

  const handleDelete = useCallback(async () => {
    // `deleting` state flips asynchronously, so guard re-entry with a ref
    // to stop a double-fire from calling onDelete twice.
    if (!entityName || deletingRef.current) return
    deletingRef.current = true
    setDeleting(true)
    try {
      const ok = await onDelete(entityName)
      if (ok) {
        setDeleteOpen(false)
        onClose()
      }
    } finally {
      deletingRef.current = false
      setDeleting(false)
    }
  }, [entityName, onDelete, onClose])

  return { deleteOpen, setDeleteOpen, deleting, setDeleting, handleDelete }
}
