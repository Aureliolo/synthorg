import { create } from 'zustand'
import {
  applyPromotion,
  evaluatePromotion,
  getPromotionHistory,
  runPromotionCycle,
} from '@/api/endpoints/promotion'
import type {
  PromotionApplyResultDTO,
  PromotionEvaluationDTO,
  PromotionRecordDTO,
} from '@/api/types'
import type { PromotionDirection } from '@/api/types/enum-values.gen'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('promotion')

interface PromotionState {
  /** Latest eligibility evaluation, keyed implicitly to the agent it was run for. */
  evaluation: PromotionEvaluationDTO | null
  evaluating: boolean
  evaluationError: string | null

  history: readonly PromotionRecordDTO[]
  historyLoading: boolean
  historyError: string | null

  /** Whether an apply (promote / demote) mutation is in flight. */
  applying: boolean

  /** Result of the most recent cluster-wide promotion cycle. */
  cycleResult: readonly PromotionRecordDTO[] | null
  cycleRunning: boolean

  evaluate: (agentId: string, direction: PromotionDirection) => Promise<void>
  fetchHistory: (agentId: string) => Promise<void>
  apply: (
    agentId: string,
    direction: PromotionDirection,
  ) => Promise<PromotionApplyResultDTO | null>
  runCycle: () => Promise<boolean>
  reset: () => void
}

const INITIAL: Pick<
  PromotionState,
  | 'evaluation'
  | 'evaluating'
  | 'evaluationError'
  | 'history'
  | 'historyLoading'
  | 'historyError'
  | 'applying'
  | 'cycleResult'
  | 'cycleRunning'
> = {
  evaluation: null,
  evaluating: false,
  evaluationError: null,
  history: [],
  historyLoading: false,
  historyError: null,
  applying: false,
  cycleResult: null,
  cycleRunning: false,
}

export const usePromotionStore = create<PromotionState>((set, get) => ({
  ...INITIAL,

  reset: () => set({ ...INITIAL }),

  evaluate: async (agentId, direction) => {
    set({ evaluating: true, evaluationError: null })
    try {
      const evaluation = await evaluatePromotion(agentId, direction)
      set({ evaluation, evaluating: false })
    } catch (err) {
      log.error('evaluate failed', sanitizeForLog(getErrorMessage(err)))
      set({ evaluating: false, evaluationError: getErrorMessage(err) })
    }
  },

  fetchHistory: async (agentId) => {
    set({ historyLoading: true, historyError: null })
    try {
      const history = await getPromotionHistory(agentId)
      set({ history, historyLoading: false })
    } catch (err) {
      log.error('fetchHistory failed', sanitizeForLog(getErrorMessage(err)))
      set({ historyLoading: false, historyError: getErrorMessage(err) })
    }
  },

  apply: async (agentId, direction) => {
    // Guard re-entry: a rapid double-invocation before the UI disable
    // propagates would otherwise send duplicate non-idempotent mutations.
    if (get().applying) return null
    set({ applying: true })
    try {
      const result = await applyPromotion(agentId, direction)
      const noun = direction === 'promotion' ? 'Promotion' : 'Demotion'
      useToastStore.getState().add({
        variant: 'success',
        title: result.applied !== null ? `${noun} applied` : `${noun} requested`,
        description:
          result.applied !== null
            ? `${result.applied.agent_name}: ${result.applied.old_level} -> ${result.applied.new_level}.`
            : 'Awaiting approval before the change takes effect.',
      })
      // Refresh the per-agent record so the history section reflects the change.
      await get().fetchHistory(agentId)
      set({ applying: false })
      return result
    } catch (err) {
      log.error('apply failed', sanitizeForLog(getErrorMessage(err)))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Could not apply seniority change'),
        description: getErrorMessage(err),
      })
      set({ applying: false })
      return null
    }
  },

  runCycle: async () => {
    // Guard re-entry: a rapid double-invocation before the UI disable
    // propagates would otherwise trigger duplicate cluster-wide cycles.
    if (get().cycleRunning) return false
    set({ cycleRunning: true })
    try {
      const records = await runPromotionCycle()
      useToastStore.getState().add({
        variant: 'success',
        title: 'Promotion cycle complete',
        description: `${records.length} seniority change(s) applied.`,
      })
      set({ cycleResult: records, cycleRunning: false })
      return true
    } catch (err) {
      log.error('runCycle failed', sanitizeForLog(getErrorMessage(err)))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Could not run promotion cycle'),
        description: getErrorMessage(err),
      })
      set({ cycleRunning: false })
      return false
    }
  },
}))
