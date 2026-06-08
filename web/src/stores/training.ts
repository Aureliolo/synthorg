import axios from 'axios'
import type { StoreApi } from 'zustand'
import { create } from 'zustand'
import { useShallow } from 'zustand/react/shallow'

import {
  createTrainingPlan,
  executeTrainingPlan,
  getLatestTrainingPlan,
  getTrainingResult,
  previewTrainingPlan,
  updateTrainingOverrides,
  type TrainingOverridesRequest,
  type TrainingPlanRequest,
  type TrainingPlanResponse,
  type TrainingResultResponse,
} from '@/api/endpoints/training'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { useToastStore } from '@/stores/toast'

const log = createLogger('training')

type PerAgent<T> = Readonly<Record<string, T | null>>
type LoadingMap = Readonly<Record<string, boolean>>
type ErrorMap = Readonly<Record<string, string | null>>
type TokenMap = Readonly<Record<string, number>>

interface TrainingState {
  plansByAgent: PerAgent<TrainingPlanResponse>
  resultsByAgent: PerAgent<TrainingResultResponse>
  planLoading: LoadingMap
  resultLoading: LoadingMap
  planError: ErrorMap
  resultError: ErrorMap
  planRequestTokens: TokenMap
  resultRequestTokens: TokenMap

  fetchPlan: (agentId: string) => Promise<void>
  fetchResult: (agentId: string) => Promise<void>
  hydrateForAgent: (agentId: string) => Promise<void>
  createPlan: (
    agentId: string,
    overrides: TrainingPlanRequest,
  ) => Promise<TrainingPlanResponse | null>
  executePlan: (agentId: string) => Promise<TrainingResultResponse | null>
  previewPlan: (agentId: string) => Promise<TrainingResultResponse | null>
  updateOverrides: (
    agentId: string,
    planId: string,
    data: TrainingOverridesRequest,
  ) => Promise<TrainingPlanResponse | null>
}

type TrainingSet = StoreApi<TrainingState>['setState']
type TrainingGet = StoreApi<TrainingState>['getState']

function setMap<V>(
  map: Readonly<Record<string, V>>,
  key: string,
  value: V,
): Readonly<Record<string, V>> {
  return { ...map, [key]: value }
}

function bumpToken(
  map: TokenMap,
  key: string,
): TokenMap {
  return setMap(map, key, (map[key] ?? 0) + 1)
}

/** 404 = nothing persisted yet for this agent; clear and suppress error. */
function isExpectedNotFound(err: unknown): boolean {
  return axios.isAxiosError(err) && err.response?.status === 404
}

async function fetchPlanImpl(
  set: TrainingSet,
  get: TrainingGet,
  agentId: string,
): Promise<void> {
  const token = (get().planRequestTokens[agentId] ?? 0) + 1
  set((state) => ({
    planLoading: setMap(state.planLoading, agentId, true),
    planError: setMap(state.planError, agentId, null),
    planRequestTokens: setMap(state.planRequestTokens, agentId, token),
  }))
  const isCurrent = () =>
    get().planRequestTokens[agentId] === token
  try {
    const plan = await getLatestTrainingPlan(agentId)
    if (!isCurrent()) return
    set((state) => ({
      plansByAgent: setMap(state.plansByAgent, agentId, plan),
      planLoading: setMap(state.planLoading, agentId, false),
      planError: setMap(state.planError, agentId, null),
    }))
  } catch (err) {
    if (!isCurrent()) return
    if (isExpectedNotFound(err)) {
      set((state) => ({
        plansByAgent: setMap(state.plansByAgent, agentId, null),
        planLoading: setMap(state.planLoading, agentId, false),
        planError: setMap(state.planError, agentId, null),
      }))
      return
    }
    log.error(
      'fetchPlan failed',
      sanitizeForLog({ agentId, err, message: getErrorMessage(err) }),
    )
    set((state) => ({
      planLoading: setMap(state.planLoading, agentId, false),
      planError: setMap(state.planError, agentId, getErrorMessage(err)),
    }))
  }
}

async function fetchResultImpl(
  set: TrainingSet,
  get: TrainingGet,
  agentId: string,
): Promise<void> {
  const token = (get().resultRequestTokens[agentId] ?? 0) + 1
  set((state) => ({
    resultLoading: setMap(state.resultLoading, agentId, true),
    resultError: setMap(state.resultError, agentId, null),
    resultRequestTokens: setMap(state.resultRequestTokens, agentId, token),
  }))
  const isCurrent = () =>
    get().resultRequestTokens[agentId] === token
  try {
    const result = await getTrainingResult(agentId)
    if (!isCurrent()) return
    set((state) => ({
      resultsByAgent: setMap(state.resultsByAgent, agentId, result),
      resultLoading: setMap(state.resultLoading, agentId, false),
      resultError: setMap(state.resultError, agentId, null),
    }))
  } catch (err) {
    if (!isCurrent()) return
    if (isExpectedNotFound(err)) {
      set((state) => ({
        resultsByAgent: setMap(state.resultsByAgent, agentId, null),
        resultLoading: setMap(state.resultLoading, agentId, false),
        resultError: setMap(state.resultError, agentId, null),
      }))
      return
    }
    const message = getErrorMessage(err)
    log.error(
      'fetchResult failed',
      sanitizeForLog({ agentId, err, message }),
    )
    set((state) => ({
      resultLoading: setMap(state.resultLoading, agentId, false),
      resultError: setMap(state.resultError, agentId, message),
    }))
  }
}

async function createPlanImpl(
  set: TrainingSet,
  agentId: string,
  overrides: TrainingPlanRequest,
): Promise<TrainingPlanResponse | null> {
  try {
    const plan = await createTrainingPlan(agentId, overrides)
    set((state) => ({
      plansByAgent: setMap(state.plansByAgent, agentId, plan),
      resultsByAgent: setMap(state.resultsByAgent, agentId, null),
      planError: setMap(state.planError, agentId, null),
      resultError: setMap(state.resultError, agentId, null),
      planLoading: setMap(state.planLoading, agentId, false),
      resultLoading: setMap(state.resultLoading, agentId, false),
      planRequestTokens: bumpToken(state.planRequestTokens, agentId),
      resultRequestTokens: bumpToken(state.resultRequestTokens, agentId),
    }))
    useToastStore.getState().add({
      variant: 'success',
      title: 'Training plan created',
    })
    return plan
  } catch (err) {
    log.error(
      'createPlan failed',
      sanitizeForLog({ agentId, err, message: getErrorMessage(err) }),
    )
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to create training plan'),
      description: getErrorMessage(err),
    })
    return null
  }
}

function buildExecutedPlanPatch(
  state: TrainingState,
  agentId: string,
  result: TrainingResultResponse,
): Partial<TrainingState> {
  const next: Partial<TrainingState> = {
    resultsByAgent: setMap(state.resultsByAgent, agentId, result),
    resultError: setMap(state.resultError, agentId, null),
    planError: setMap(state.planError, agentId, null),
    planLoading: setMap(state.planLoading, agentId, false),
    resultLoading: setMap(state.resultLoading, agentId, false),
    planRequestTokens: bumpToken(state.planRequestTokens, agentId),
    resultRequestTokens: bumpToken(state.resultRequestTokens, agentId),
  }
  const cached = state.plansByAgent[agentId]
  if (cached) {
    next.plansByAgent = setMap(state.plansByAgent, agentId, {
      ...cached,
      status: 'executed',
      executed_at: result.completed_at,
    })
  }
  return next
}

async function executePlanImpl(
  set: TrainingSet,
  agentId: string,
): Promise<TrainingResultResponse | null> {
  try {
    const result = await executeTrainingPlan(agentId)
    set((state) => buildExecutedPlanPatch(state, agentId, result))
    useToastStore.getState().add({
      variant: 'success',
      title: 'Training executed',
    })
    return result
  } catch (err) {
    log.error(
      'executePlan failed',
      sanitizeForLog({ agentId, err, message: getErrorMessage(err) }),
    )
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Training execution failed'),
      description: getErrorMessage(err),
    })
    return null
  }
}

async function previewPlanImpl(
  agentId: string,
): Promise<TrainingResultResponse | null> {
  try {
    return await previewTrainingPlan(agentId)
  } catch (err) {
    log.error(
      'previewPlan failed',
      sanitizeForLog({ agentId, err, message: getErrorMessage(err) }),
    )
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Training preview failed'),
      description: getErrorMessage(err),
    })
    return null
  }
}

async function updateOverridesImpl(
  set: TrainingSet,
  agentId: string,
  planId: string,
  data: TrainingOverridesRequest,
): Promise<TrainingPlanResponse | null> {
  try {
    const plan = await updateTrainingOverrides(agentId, planId, data)
    set((state) => ({
      plansByAgent: setMap(state.plansByAgent, agentId, plan),
      planError: setMap(state.planError, agentId, null),
      planLoading: setMap(state.planLoading, agentId, false),
      planRequestTokens: bumpToken(state.planRequestTokens, agentId),
    }))
    useToastStore.getState().add({
      variant: 'success',
      title: 'Overrides saved',
    })
    return plan
  } catch (err) {
    log.error(
      'updateOverrides failed',
      sanitizeForLog({
        agentId,
        planId,
        err,
        message: getErrorMessage(err),
      }),
    )
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to save overrides'),
      description: getErrorMessage(err),
    })
    return null
  }
}

export const useTrainingStore = create<TrainingState>()((set, get) => ({
  plansByAgent: {},
  resultsByAgent: {},
  planLoading: {},
  resultLoading: {},
  planError: {},
  resultError: {},
  planRequestTokens: {},
  resultRequestTokens: {},

  fetchPlan: (agentId) => fetchPlanImpl(set, get, agentId),
  fetchResult: (agentId) => fetchResultImpl(set, get, agentId),
  hydrateForAgent: async (agentId) => {
    await Promise.all([
      get().fetchPlan(agentId),
      get().fetchResult(agentId),
    ])
  },
  createPlan: (agentId, overrides) =>
    createPlanImpl(set, agentId, overrides),
  executePlan: (agentId) => executePlanImpl(set, agentId),
  previewPlan: (agentId) => previewPlanImpl(agentId),
  updateOverrides: (agentId, planId, data) =>
    updateOverridesImpl(set, agentId, planId, data),
}))

export interface TrainingForAgent {
  plan: TrainingPlanResponse | null
  result: TrainingResultResponse | null
  /** True while either plan or result is in-flight for this agent. */
  loading: boolean
  /** Plan fetch in-flight. */
  planLoading: boolean
  /** Result fetch in-flight. */
  resultLoading: boolean
  /** First non-null error across plan/result (plan wins if both set). */
  error: string | null
  planError: string | null
  resultError: string | null
}

interface AgentLoadingPair {
  planLoading: boolean
  resultLoading: boolean
}

interface AgentErrorPair {
  planError: string | null
  resultError: string | null
}

function pickAgentLoading(
  state: TrainingState,
  agentId: string,
): AgentLoadingPair {
  return {
    planLoading: state.planLoading[agentId] ?? false,
    resultLoading: state.resultLoading[agentId] ?? false,
  }
}

function pickAgentErrors(
  state: TrainingState,
  agentId: string,
): AgentErrorPair {
  return {
    planError: state.planError[agentId] ?? null,
    resultError: state.resultError[agentId] ?? null,
  }
}

function selectTrainingForAgent(
  state: TrainingState,
  agentId: string,
): TrainingForAgent {
  const loading = pickAgentLoading(state, agentId)
  const errors = pickAgentErrors(state, agentId)
  return {
    plan: state.plansByAgent[agentId] ?? null,
    result: state.resultsByAgent[agentId] ?? null,
    loading: loading.planLoading || loading.resultLoading,
    ...loading,
    error: errors.planError ?? errors.resultError,
    ...errors,
  }
}

/**
 * Subscribe to the training state for a single agent. Uses
 * ``useShallow`` so re-renders only fire when one of the underlying
 * fields changes, not on every store update.
 */
export function useTrainingForAgent(agentId: string): TrainingForAgent {
  return useTrainingStore(
    useShallow((state) => selectTrainingForAgent(state, agentId)),
  )
}
