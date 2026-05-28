import { useCallback, useState } from 'react'

import { useCustomRulesStore } from '@/stores/custom-rules'
import { useToastStore } from '@/stores/toast'
import type { CustomRule } from '@/api/endpoints/custom-rules'

export type RulesDrawerView = 'list' | 'builder'

export interface RulesDrawerState {
  view: RulesDrawerView
  editRule: CustomRule | null
  deleteTarget: string | null
  deleting: boolean
  handleCreateClick: () => void
  handleEditClick: (id: string) => void
  handleBuilderClose: () => Promise<void>
  handleToggle: (name: string, id?: string) => Promise<void>
  handleDeleteRequest: (id: string) => void
  handleDeleteCancel: () => void
  handleDeleteConfirm: () => Promise<boolean>
  handleDrawerClose: () => void
}

interface UseRulesDrawerStateArgs {
  onClose: () => void
  onRefresh: () => Promise<void>
}

export function useRulesDrawerState({
  onClose,
  onRefresh,
}: UseRulesDrawerStateArgs): RulesDrawerState {
  const addToast = useToastStore((s) => s.add)
  const [view, setView] = useState<RulesDrawerView>('list')
  const [editRule, setEditRule] = useState<CustomRule | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  const customRules = useCustomRulesStore((s) => s.rules)
  const toggleRule = useCustomRulesStore((s) => s.toggleRule)
  const deleteRule = useCustomRulesStore((s) => s.deleteRule)

  const safeRefresh = useCallback(async () => {
    try {
      await onRefresh()
    } catch {
      // onRefresh typically loads the rule list from a parent page; a transient
      // failure there shouldn't swallow the mutation's success, but the user
      // still needs to know the list may be stale.
      addToast({
        variant: 'error',
        title: 'Could not refresh',
        description: 'Try again in a moment.',
      })
    }
  }, [onRefresh, addToast])

  const handleCreateClick = useCallback(() => {
    setEditRule(null)
    setView('builder')
  }, [])

  const handleEditClick = useCallback(
    (id: string) => {
      const rule = customRules.find((r) => r.id === id)
      if (rule) {
        setEditRule(rule)
        setView('builder')
      }
    },
    [customRules],
  )

  const handleBuilderClose = useCallback(async () => {
    setView('list')
    setEditRule(null)
    await safeRefresh()
  }, [safeRefresh])

  const handleToggle = useCallback(
    async (_name: string, id?: string) => {
      if (!id) return
      // Sentinel-return contract: the store owns success/error toasts.
      const toggled = await toggleRule(id)
      if (toggled) await safeRefresh()
    },
    [toggleRule, safeRefresh],
  )

  const handleDeleteConfirm = useDeleteConfirmCallback(
    deleteTarget,
    deleteRule,
    setDeleting,
    setDeleteTarget,
    safeRefresh,
  )

  const handleDrawerClose = useCallback(() => {
    setView('list')
    setEditRule(null)
    setDeleteTarget(null)
    setDeleting(false)
    onClose()
  }, [onClose])

  return {
    view,
    editRule,
    deleteTarget,
    deleting,
    handleCreateClick,
    handleEditClick,
    handleBuilderClose,
    handleToggle,
    handleDeleteRequest: setDeleteTarget,
    handleDeleteCancel: () => setDeleteTarget(null),
    handleDeleteConfirm,
    handleDrawerClose,
  }
}

function useDeleteConfirmCallback(
  deleteTarget: string | null,
  deleteRule: ReturnType<typeof useCustomRulesStore.getState>['deleteRule'],
  setDeleting: (v: boolean) => void,
  setDeleteTarget: (v: string | null) => void,
  safeRefresh: () => Promise<void>,
): () => Promise<boolean> {
  return useCallback(async () => {
    if (!deleteTarget) return false
    setDeleting(true)
    // Sentinel-return contract: the store emits success and error toasts. On
    // failure, leave the dialog open so the user can retry without losing
    // their place (return false tells ConfirmDialog not to auto-close).
    const ok = await deleteRule(deleteTarget)
    setDeleting(false)
    if (!ok) return false
    setDeleteTarget(null)
    await safeRefresh()
    return true
  }, [deleteTarget, deleteRule, setDeleting, setDeleteTarget, safeRefresh])
}
