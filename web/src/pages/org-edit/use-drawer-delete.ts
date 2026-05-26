import { useCallback, useState } from 'react'

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

  const handleDelete = useCallback(async () => {
    if (!entityName) return
    setDeleting(true)
    try {
      const ok = await onDelete(entityName)
      if (ok) {
        setDeleteOpen(false)
        onClose()
      }
    } finally {
      setDeleting(false)
    }
  }, [entityName, onDelete, onClose])

  return { deleteOpen, setDeleteOpen, deleting, setDeleting, handleDelete }
}
