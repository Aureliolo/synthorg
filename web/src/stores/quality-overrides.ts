/**
 * Per-agent quality-score override store.
 *
 * Owns the toast / error UX for the three quality-override endpoints
 * so the {@link QualityScoreOverride} component stays presentational.
 * Follows the canonical store error contract (try/catch -> log + toast
 * -> sentinel return) -- callers MUST NOT wrap these in try/catch.
 *
 * The `getOverride` action specifically does NOT toast on a 404, since
 * "no active override for this agent" is the steady-state for most
 * agents and is communicated through the `null` return.
 */

import { create } from 'zustand'
import {
  clearQualityOverride as apiClear,
  getQualityOverride as apiGet,
  setQualityOverride as apiSet,
} from '@/api/endpoints/quality'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage, isAxiosError } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type { OverrideResponse, SetOverrideRequest } from '@/api/types/collaboration'

const log = createLogger('quality-overrides')

/**
 * Discriminated result for `getOverride`. The three cases are
 * semantically distinct -- an override present, a 404 (steady-state
 * for most agents, not an error), and a non-404 load failure -- and
 * collapsing them onto `null` caused the page to render the "no active
 * override" form after a 500, inviting the user to overwrite an
 * override that merely failed to load.
 */
export type GetOverrideResult =
  | { kind: 'ok'; data: OverrideResponse }
  | { kind: 'missing' }
  | { kind: 'error' }

interface QualityOverridesState {
  /**
   * Fetch the active override for an agent. Returns a discriminated
   * result so the component can tell "no override" apart from "load
   * failed". A 404 resolves to `{ kind: 'missing' }` without toasting;
   * any other failure toasts an error and returns `{ kind: 'error' }`.
   */
  getOverride: (agentId: string) => Promise<GetOverrideResult>
  setOverride: (
    agentId: string,
    data: SetOverrideRequest,
  ) => Promise<OverrideResponse | null>
  clearOverride: (agentId: string) => Promise<boolean>
}

export const useQualityOverridesStore = create<QualityOverridesState>()(() => ({
  getOverride: async (agentId) => {
    try {
      const data = await apiGet(agentId)
      return { kind: 'ok', data }
    } catch (err) {
      if (isAxiosError(err) && err.response?.status === 404) {
        return { kind: 'missing' }
      }
      log.error('Get quality override failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to load quality override'),
        description: getErrorMessage(err),
      })
      return { kind: 'error' }
    }
  },

  setOverride: async (agentId, data) => {
    try {
      const result = await apiSet(agentId, data)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Quality override applied',
      })
      return result
    } catch (err) {
      log.error('Set quality override failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to apply quality override'),
        description: getErrorMessage(err),
      })
      return null
    }
  },

  clearOverride: async (agentId) => {
    try {
      await apiClear(agentId)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Quality override cleared',
      })
      return true
    } catch (err) {
      log.error('Clear quality override failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to clear quality override'),
        description: getErrorMessage(err),
      })
      return false
    }
  },
}))
