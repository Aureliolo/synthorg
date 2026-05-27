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
  // Holds the entity name whose delete is in flight (null when idle), so a
  // mid-delete target switch can release the guard and a stale `finally`
  // from the old request never clears a newer in-flight delete.
  const deletingRef = useRef<string | null>(null)
  // Latest entity name, read inside handleDelete after the await to detect
  // a target switch that happened while onDelete was in flight.
  const entityNameRef = useRef(entityName)

  // Reset the confirm dialog and spinner when the drawer switches to a
  // different entity, so a stale open dialog or spinner never leaks
  // across targets (react.dev "Adjusting some state when a prop changes").
  const [prevEntityName, setPrevEntityName] = useState(entityName)
  if (entityName !== prevEntityName) {
    setPrevEntityName(entityName)
    entityNameRef.current = entityName
    deletingRef.current = null
    setDeleteOpen(false)
    setDeleting(false)
  }

  const handleDelete = useCallback(async () => {
    // `deleting` state flips asynchronously, so guard re-entry with a ref
    // to stop a double-fire from calling onDelete twice.
    const targetName = entityNameRef.current
    if (!targetName || deletingRef.current !== null) return
    deletingRef.current = targetName
    setDeleting(true)
    try {
      const ok = await onDelete(targetName)
      // Only apply post-success UI if the drawer still targets the entity
      // we deleted; otherwise a stale success would close the new drawer.
      if (ok && entityNameRef.current === targetName) {
        setDeleteOpen(false)
        onClose()
      }
    } finally {
      // Only release if this delete still owns the guard; a target switch
      // mid-flight resets it and may have started a new delete.
      if (deletingRef.current === targetName) {
        deletingRef.current = null
        setDeleting(false)
      }
    }
  }, [onDelete, onClose])

  return { deleteOpen, setDeleteOpen, deleting, setDeleting, handleDelete }
}
