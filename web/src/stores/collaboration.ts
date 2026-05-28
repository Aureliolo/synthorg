/**
 * Per-agent collaboration-override store.
 *
 * Owns the toast / error UX for reading and clearing the collaboration
 * score override so {@link CollaborationPanel} stays presentational.
 * Follows the canonical store error contract (try/catch -> log + toast
 * -> sentinel return) -- callers MUST NOT wrap these in try/catch.
 *
 * As with quality overrides, `getOverride` does NOT toast on a 404,
 * since "no active override for this agent" is the steady-state and is
 * communicated through the `{ kind: 'missing' }` result.
 */

import { create } from 'zustand'
import {
  clearOverride as apiClear,
  getOverride as apiGet,
} from '@/api/endpoints/collaboration'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage, isAxiosError } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type { OverrideResponse } from '@/api/types/collaboration'

const log = createLogger('collaboration-overrides')

/**
 * Discriminated result for `getOverride`: an override present, a 404
 * (steady-state, not an error), or a non-404 load failure. Keeping the
 * cases distinct stops the panel rendering "no override" after a 500.
 */
export type GetOverrideResult =
  | { kind: 'ok'; data: OverrideResponse }
  | { kind: 'missing' }
  | { kind: 'error' }

interface CollaborationState {
  getOverride: (agentId: string) => Promise<GetOverrideResult>
  clearOverride: (agentId: string) => Promise<boolean>
}

export const useCollaborationStore = create<CollaborationState>()(() => ({
  getOverride: async (agentId) => {
    try {
      const data = await apiGet(agentId)
      return { kind: 'ok', data }
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 404) {
        return { kind: 'missing' }
      }
      log.error('Get collaboration override failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to load collaboration override',
        description: getErrorMessage(err),
      })
      return { kind: 'error' }
    }
  },

  clearOverride: async (agentId) => {
    try {
      await apiClear(agentId)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Collaboration override cleared',
      })
      return true
    } catch (err) {
      log.error('Clear collaboration override failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to clear collaboration override'),
        description: getErrorMessage(err),
      })
      return false
    }
  },
}))
