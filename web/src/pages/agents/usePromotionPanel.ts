import { useCallback, useEffect, useState } from 'react'

import { useAuth } from '@/hooks/useAuth'
import { usePromotionStore } from '@/stores/promotion'
import type { PromotionEvaluationDTO, PromotionRecordDTO } from '@/api/types'
import type { PromotionDirection } from '@/api/types/enum-values.gen'
import { hasPrivilegedRole } from '@/utils/roles'

export interface PromotionPanelController {
  canManage: boolean
  evaluation: PromotionEvaluationDTO | null
  evaluating: boolean
  evaluationError: string | null
  history: readonly PromotionRecordDTO[]
  historyLoading: boolean
  historyError: string | null
  applying: boolean
  drawerOpen: boolean
  setDrawerOpen: (open: boolean) => void
  /** Direction whose apply confirmation is open, or null when closed. */
  pendingDirection: PromotionDirection | null
  requestApply: (direction: PromotionDirection) => void
  cancelApply: () => void
  checkEligibility: (direction: PromotionDirection) => Promise<void>
  confirmApply: () => Promise<boolean>
  retryHistory: () => Promise<void>
}

export function usePromotionPanel(agentId: string): PromotionPanelController {
  const { userRole } = useAuth()
  const canManage = hasPrivilegedRole(userRole)

  const evaluation = usePromotionStore((s) => s.evaluation)
  const evaluating = usePromotionStore((s) => s.evaluating)
  const evaluationError = usePromotionStore((s) => s.evaluationError)
  const history = usePromotionStore((s) => s.history)
  const historyLoading = usePromotionStore((s) => s.historyLoading)
  const historyError = usePromotionStore((s) => s.historyError)
  const applying = usePromotionStore((s) => s.applying)
  const evaluate = usePromotionStore((s) => s.evaluate)
  const fetchHistory = usePromotionStore((s) => s.fetchHistory)
  const apply = usePromotionStore((s) => s.apply)
  const reset = usePromotionStore((s) => s.reset)

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [pendingDirection, setPendingDirection] = useState<PromotionDirection | null>(null)

  // Load history for the active agent and clear the singleton store on unmount
  // / agent change so a stale evaluation cannot leak across agents. The
  // confirmation-local state (drawerOpen / pendingDirection) is reset by the
  // ``key={agent.id}`` remount at the call site rather than syncing it here.
  useEffect(() => {
    void fetchHistory(agentId)
    return () => reset()
  }, [agentId, fetchHistory, reset])

  // Clear the pending direction whenever the drawer closes so a stale
  // confirmation cannot be applied after the operator dismisses it.
  const handleDrawerOpen = useCallback((open: boolean) => {
    setDrawerOpen(open)
    if (!open) setPendingDirection(null)
  }, [])

  const checkEligibility = useCallback(
    async (direction: PromotionDirection) => {
      setDrawerOpen(true)
      await evaluate(agentId, direction)
    },
    [agentId, evaluate],
  )

  const confirmApply = useCallback(async (): Promise<boolean> => {
    if (pendingDirection === null) return false
    const result = await apply(agentId, pendingDirection)
    // Returning `false` keeps the confirm dialog open so the operator can
    // retry; the dialog closes itself on a non-`false` (success) return.
    return result !== null
  }, [agentId, apply, pendingDirection])

  const retryHistory = useCallback(() => fetchHistory(agentId), [agentId, fetchHistory])

  return {
    canManage,
    evaluation,
    evaluating,
    evaluationError,
    history,
    historyLoading,
    historyError,
    applying,
    drawerOpen,
    setDrawerOpen: handleDrawerOpen,
    pendingDirection,
    requestApply: setPendingDirection,
    cancelApply: () => {
      setDrawerOpen(false)
      setPendingDirection(null)
    },
    checkEligibility,
    confirmApply,
    retryHistory,
  }
}
