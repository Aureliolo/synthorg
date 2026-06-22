import { useCallback, useEffect, useState } from 'react'

import { useAuth } from '@/hooks/useAuth'
import { usePromotionStore } from '@/stores/promotion'
import type { PromotionEvaluationDTO, PromotionRecordDTO } from '@/api/types'
import type { PromotionDirection } from '@/api/types/enum-values.gen'

const PROMOTION_ROLES = ['ceo', 'manager'] as const

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
  confirmApply: () => Promise<void>
  retryHistory: () => Promise<void>
}

export function usePromotionPanel(agentId: string): PromotionPanelController {
  const { userRole } = useAuth()
  const canManage = userRole !== null && (PROMOTION_ROLES as readonly string[]).includes(userRole)

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
  // / agent change so a stale evaluation cannot leak across agents.
  useEffect(() => {
    void fetchHistory(agentId)
    return () => reset()
  }, [agentId, fetchHistory, reset])

  const checkEligibility = useCallback(
    async (direction: PromotionDirection) => {
      setDrawerOpen(true)
      await evaluate(agentId, direction)
    },
    [agentId, evaluate],
  )

  const confirmApply = useCallback(async () => {
    if (pendingDirection === null) return
    const result = await apply(agentId, pendingDirection)
    if (result !== null) setPendingDirection(null)
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
    setDrawerOpen,
    pendingDirection,
    requestApply: setPendingDirection,
    cancelApply: () => setPendingDirection(null),
    checkEligibility,
    confirmApply,
    retryHistory,
  }
}
