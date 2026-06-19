import { create, type StoreApi } from 'zustand'

import { ApiRequestError } from '@/api/client'
import * as ceremonyApi from '@/api/endpoints/ceremony-policy'
import type {
  ActiveCeremonyStrategy,
  CeremonyPolicyConfig,
  ResolvedCeremonyPolicyResponse,
} from '@/api/types/ceremony-policy'
import { getErrorMessage } from '@/utils/errors'
import { useToastStore } from '@/stores/toast'

/**
 * Friendly message for the CAS conflict raised by the department
 * ceremony-policy endpoints when two operators save simultaneously.
 *
 * The backend uses a bounded compare-and-swap loop against the
 * ``coordination.dept_ceremony_policies`` JSON blob and surfaces a
 * ``VersionConflictError`` (HTTP 409, ``error_category: "conflict"``)
 * once the retry cap is exhausted.  See
 * ``docs/design/persistence.md`` → "Cross-worker CAS on JSON-blob
 * settings".
 */
const CEREMONY_POLICY_CONFLICT_MESSAGE =
  'Another operator updated this department\'s ceremony policy at the same time. Reload the page and reapply your change.'

function describeCeremonyPolicySaveError(err: unknown): string {
  if (err instanceof ApiRequestError && err.errorDetail?.error_category === 'conflict') {
    return CEREMONY_POLICY_CONFLICT_MESSAGE
  }
  return getErrorMessage(err)
}

interface CeremonyPolicyState {
  /** Resolved ceremony policy with field origins (may include department overlay). */
  resolvedPolicy: ResolvedCeremonyPolicyResponse | null
  /** Currently active (locked) strategy in the running sprint. */
  activeStrategy: ActiveCeremonyStrategy | null
  /** Per-department ceremony policy overrides (keyed by department name). */
  departmentPolicies: ReadonlyMap<string, CeremonyPolicyConfig | null>
  /** Whether the initial fetch is in progress. */
  loading: boolean
  /** Error from the most recent resolved policy fetch. */
  error: string | null
  /** Error from the most recent active strategy fetch. */
  activeStrategyError: string | null
  /** Error from the most recent per-department fetch (keyed by dept name). */
  departmentErrors: ReadonlyMap<string, string>
  /** Whether a save operation is in progress. */
  saving: boolean
  /** Error from the most recent save attempt. */
  saveError: string | null

  /** Fetch the resolved project-level policy, optionally with department overlay. */
  fetchResolvedPolicy: (department?: string) => Promise<void>
  /** Fetch the currently active sprint strategy. */
  fetchActiveStrategy: () => Promise<void>
  /** Fetch a department's ceremony policy override. */
  fetchDepartmentPolicy: (name: string) => Promise<void>
  /** Set a department ceremony policy override. Returns false on failure. */
  updateDepartmentPolicy: (name: string, data: CeremonyPolicyConfig) => Promise<boolean>
  /** Clear a department's ceremony policy override (revert to inherit). Returns false on failure. */
  clearDepartmentPolicy: (name: string) => Promise<boolean>
}

type CeremonySet = StoreApi<CeremonyPolicyState>['setState']
type CeremonyGet = StoreApi<CeremonyPolicyState>['getState']

async function _updateDepartmentPolicy(
  set: CeremonySet,
  get: CeremonyGet,
  name: string,
  data: CeremonyPolicyConfig,
): Promise<boolean> {
  set({ saving: true, saveError: null })
  try {
    const saved = await ceremonyApi.updateDepartmentCeremonyPolicy(name, data)
    const updated = new Map(get().departmentPolicies)
    // Use server-normalized response instead of the input data
    updated.set(name, saved)
    set({ departmentPolicies: updated, saving: false })
    useToastStore.getState().add({
      variant: 'success',
      title: 'Ceremony policy saved',
      description: `Updated the override for ${name}.`,
    })
    return true
  } catch (err) {
    const message = describeCeremonyPolicySaveError(err)
    set({ saveError: message, saving: false })
    useToastStore.getState().add({
      variant: 'error',
      title: 'Could not save ceremony policy',
      description: message,
    })
    return false
  }
}

async function _clearDepartmentPolicy(
  set: CeremonySet,
  get: CeremonyGet,
  name: string,
): Promise<boolean> {
  set({ saving: true, saveError: null })
  try {
    await ceremonyApi.clearDepartmentCeremonyPolicy(name)
    const updated = new Map(get().departmentPolicies)
    updated.set(name, null)
    set({ departmentPolicies: updated, saving: false })
    useToastStore.getState().add({
      variant: 'success',
      title: 'Ceremony policy cleared',
      description: `${name} now inherits the project-level policy.`,
    })
    return true
  } catch (err) {
    const message = describeCeremonyPolicySaveError(err)
    set({ saveError: message, saving: false })
    useToastStore.getState().add({
      variant: 'error',
      title: 'Could not clear ceremony policy',
      description: message,
    })
    return false
  }
}

export const useCeremonyPolicyStore = create<CeremonyPolicyState>()((set, get) => ({
  resolvedPolicy: null,
  activeStrategy: null,
  departmentPolicies: new Map(),
  loading: false,
  error: null,
  activeStrategyError: null,
  departmentErrors: new Map(),
  saving: false,
  saveError: null,

  fetchResolvedPolicy: async (department?: string) => {
    set({ loading: true, error: null })
    try {
      const resolved = await ceremonyApi.getResolvedPolicy(department)
      set({ resolvedPolicy: resolved, loading: false })
    } catch (err) {
      set({ error: getErrorMessage(err), loading: false })
    }
  },

  fetchActiveStrategy: async () => {
    set({ activeStrategyError: null })
    try {
      const active = await ceremonyApi.getActiveStrategy()
      set({ activeStrategy: active })
    } catch (err) {
      set({ activeStrategyError: getErrorMessage(err) })
    }
  },

  fetchDepartmentPolicy: async (name: string) => {
    try {
      const policy = await ceremonyApi.getDepartmentCeremonyPolicy(name)
      const current = get().departmentPolicies
      const updated = new Map(current)
      updated.set(name, policy)
      // Clear any previous error for this department
      const errors = new Map(get().departmentErrors)
      errors.delete(name)
      set({ departmentPolicies: updated, departmentErrors: errors })
    } catch (err) {
      const errors = new Map(get().departmentErrors)
      errors.set(name, getErrorMessage(err))
      set({ departmentErrors: errors })
    }
  },

  updateDepartmentPolicy: (name: string, data: CeremonyPolicyConfig) =>
    _updateDepartmentPolicy(set, get, name, data),

  clearDepartmentPolicy: (name: string) => _clearDepartmentPolicy(set, get, name),
}))
