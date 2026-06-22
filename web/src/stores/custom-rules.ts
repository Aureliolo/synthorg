import type { StoreApi } from 'zustand'
import { create } from 'zustand'

import {
  createCustomRule,
  deleteCustomRule,
  listCustomRules,
  listMetrics,
  previewRule,
  toggleCustomRule,
  updateCustomRule,
  type CreateCustomRuleRequest,
  type CustomRule,
  type MetricDescriptor,
  type PreviewRequest,
  type PreviewResult,
} from '@/api/endpoints/custom-rules'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('custom-rules')

let _listRequestToken = 0

function isStaleListRequest(token: number): boolean {
  return _listRequestToken !== token
}

interface CustomRulesState {
  // Data
  rules: readonly CustomRule[]
  metrics: readonly MetricDescriptor[]

  // UI state
  loading: boolean
  error: string | null
  metricsLoading: boolean
  metricsError: string | null
  submitting: boolean

  // Actions. Mutations follow the canonical store error contract:
  // log + error toast + return sentinel (`null` for entity-returning,
  // `false` for delete) on failure. Callers MUST NOT wrap these in
  // try/catch.
  fetchRules: () => Promise<void>
  fetchMetrics: () => Promise<void>
  createRule: (data: CreateCustomRuleRequest) => Promise<CustomRule | null>
  updateRule: (
    id: string,
    data: Partial<CreateCustomRuleRequest>,
  ) => Promise<CustomRule | null>
  deleteRule: (id: string) => Promise<boolean>
  toggleRule: (id: string) => Promise<CustomRule | null>
  /**
   * Run the server-side preview evaluator. Intentionally re-throws on
   * error rather than adopting the sentinel-null contract the CRUD
   * mutations use: the caller surface
   * (``RulePreviewPanel``) displays the error *inline* next to the
   * preview controls rather than via a toast, so it needs the raw
   * error message and owns the ``try/catch`` boundary. Keep that
   * exception here; do not normalise it to ``null``.
   */
  previewRule: (data: PreviewRequest) => Promise<PreviewResult>
}

type CrSet = StoreApi<CustomRulesState>['setState']

async function fetchRulesImpl(set: CrSet): Promise<void> {
  const token = ++_listRequestToken
  set({ loading: true, error: null })
  try {
    const rules = await listCustomRules()
    if (isStaleListRequest(token)) return
    set({ rules, loading: false })
  } catch (err) {
    if (isStaleListRequest(token)) return
    log.error('Failed to fetch custom rules', err)
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function fetchMetricsImpl(set: CrSet): Promise<void> {
  set({ metricsLoading: true, metricsError: null })
  try {
    const metrics = await listMetrics()
    set({ metrics, metricsLoading: false })
  } catch (err) {
    log.error('Failed to fetch metrics', err)
    set({ metricsLoading: false, metricsError: getErrorMessage(err) })
  }
}

async function createRuleImpl(
  set: CrSet,
  data: CreateCustomRuleRequest,
): Promise<CustomRule | null> {
  set({ submitting: true })
  try {
    const rule = await createCustomRule(data)
    set((state) => ({ rules: [...state.rules, rule], submitting: false }))
    useToastStore.getState().add({
      variant: 'success',
      title: `Rule ${rule.name} created`,
    })
    return rule
  } catch (err) {
    log.error('Create custom rule failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to create rule'),
      description: getErrorMessage(err),
    })
    set({ submitting: false })
    return null
  }
}

async function updateRuleImpl(
  set: CrSet,
  id: string,
  data: Partial<CreateCustomRuleRequest>,
): Promise<CustomRule | null> {
  set({ submitting: true })
  try {
    const updated = await updateCustomRule(id, data)
    set((state) => ({
      rules: state.rules.map((r) => (r.id === id ? updated : r)),
      submitting: false,
    }))
    useToastStore.getState().add({
      variant: 'success',
      title: `Rule ${updated.name} updated`,
    })
    return updated
  } catch (err) {
    log.error('Update custom rule failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to update rule'),
      description: getErrorMessage(err),
    })
    set({ submitting: false })
    return null
  }
}

async function deleteRuleImpl(set: CrSet, id: string): Promise<boolean> {
  try {
    await deleteCustomRule(id)
    set((state) => ({
      rules: state.rules.filter((r) => r.id !== id),
    }))
    useToastStore.getState().add({ variant: 'success', title: 'Rule deleted' })
    return true
  } catch (err) {
    log.error('Delete custom rule failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to delete rule',
      description: getErrorMessage(err),
    })
    return false
  }
}

async function toggleRuleImpl(
  set: CrSet,
  id: string,
): Promise<CustomRule | null> {
  try {
    const toggled = await toggleCustomRule(id)
    set((state) => ({
      rules: state.rules.map((r) => (r.id === id ? toggled : r)),
    }))
    useToastStore.getState().add({
      variant: 'success',
      title: `Rule ${toggled.enabled ? 'enabled' : 'disabled'}`,
    })
    return toggled
  } catch (err) {
    log.error('Toggle custom rule failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to toggle rule',
      description: getErrorMessage(err),
    })
    return null
  }
}

export const useCustomRulesStore = create<CustomRulesState>()((set) => ({
  rules: [],
  metrics: [],
  loading: false,
  error: null,
  metricsLoading: false,
  metricsError: null,
  submitting: false,

  fetchRules: () => fetchRulesImpl(set),
  fetchMetrics: () => fetchMetricsImpl(set),
  createRule: (data) => createRuleImpl(set, data),
  updateRule: (id, data) => updateRuleImpl(set, id, data),
  deleteRule: (id) => deleteRuleImpl(set, id),
  toggleRule: (id) => toggleRuleImpl(set, id),
  previewRule: (data) => previewRule(data),
}))
