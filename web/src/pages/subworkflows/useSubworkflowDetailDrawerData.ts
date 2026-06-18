import { useCallback, useEffect, useState } from 'react'
import { listVersions, listParents } from '@/api/endpoints/subworkflows'
import { useSubworkflowsStore } from '@/stores/subworkflows'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { ParentReference, SubworkflowSummary } from '@/api/types/workflows'

const log = createLogger('SubworkflowDetailDrawer')

export interface SubworkflowDetailDrawerData {
  versions: readonly string[]
  parents: readonly ParentReference[]
  loading: boolean
  detailsLoaded: boolean
  deleteConfirmOpen: boolean
  deleting: boolean
  openDeleteConfirm: () => void
  closeDeleteConfirm: () => void
  handleDelete: () => Promise<void>
}

interface DetailsFetchSetters {
  setVersions: (v: readonly string[]) => void
  setParents: (p: readonly ParentReference[]) => void
  setLoading: (l: boolean) => void
  setLoadedKey: (k: string | null) => void
  addToast: ReturnType<typeof useToastStore.getState>['add']
}

export function useSubworkflowDetailDrawerData(
  open: boolean,
  subworkflow: SubworkflowSummary | null,
  onClose: () => void,
): SubworkflowDetailDrawerData {
  const addToast = useToastStore((s) => s.add)
  const [versions, setVersions] = useState<readonly string[]>([])
  const [parents, setParents] = useState<readonly ParentReference[]>([])
  const [loading, setLoading] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const detailsKey = subworkflow
    ? `${subworkflow.subworkflow_id}:${subworkflow.latest_version}`
    : null
  const [loadedKey, setLoadedKey] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !subworkflow) return
    const setters: DetailsFetchSetters = {
      setVersions,
      setParents,
      setLoading,
      setLoadedKey,
      addToast,
    }
    const ctrl = { cancelled: false }
    void runDetailsFetch(subworkflow, setters, ctrl)
    return () => {
      ctrl.cancelled = true
      setVersions([])
      setParents([])
      setLoadedKey(null)
      setDeleteConfirmOpen(false)
      setDeleting(false)
    }
  }, [open, subworkflow, addToast])

  const detailsLoaded = loadedKey !== null && loadedKey === detailsKey

  const handleDelete = useCallback(async () => {
    if (!subworkflow || loading || parents.length > 0 || !detailsLoaded) return
    setDeleting(true)
    try {
      const ok = await useSubworkflowsStore
        .getState()
        .deleteSubworkflow(subworkflow.subworkflow_id, subworkflow.latest_version)
      if (ok) {
        setDeleteConfirmOpen(false)
        onClose()
      }
    } finally {
      setDeleting(false)
    }
  }, [subworkflow, loading, parents, detailsLoaded, onClose])

  return {
    versions,
    parents,
    loading,
    detailsLoaded,
    deleteConfirmOpen,
    deleting,
    openDeleteConfirm: () => setDeleteConfirmOpen(true),
    closeDeleteConfirm: () => setDeleteConfirmOpen(false),
    handleDelete,
  }
}

async function runDetailsFetch(
  subworkflow: SubworkflowSummary,
  setters: DetailsFetchSetters,
  ctrl: { cancelled: boolean },
): Promise<void> {
  const subId = subworkflow.subworkflow_id
  const subVersion = subworkflow.latest_version
  const key = `${subId}:${subVersion}`
  setters.setLoadedKey(null)
  setters.setVersions([])
  setters.setParents([])
  setters.setLoading(true)
  try {
    const [v, p] = await Promise.all([
      listVersions(subId, { limit: 100 }),
      listParents(subId, subVersion, { limit: 100 }),
    ])
    if (!ctrl.cancelled) {
      setters.setVersions(v.data)
      setters.setParents(p.data)
      setters.setLoadedKey(key)
    }
  } catch (err: unknown) {
    if (!ctrl.cancelled) {
      log.warn('Failed to load subworkflow details', sanitizeForLog(err))
      setters.addToast({
        variant: 'error',
        title: 'Could not load subworkflow details',
        description: getErrorMessage(err),
        action: {
          label: 'Retry',
          onClick: () => {
            void runDetailsFetch(subworkflow, setters, { cancelled: false })
          },
        },
      })
    }
  } finally {
    if (!ctrl.cancelled) setters.setLoading(false)
  }
}
