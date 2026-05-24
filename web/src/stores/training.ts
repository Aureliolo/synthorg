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
import { getErrorMessage } from '@/utils/errors'
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

  fetchPlan: (agentName: string) => Promise<void>
  fetchResult: (agentName: string) => Promise<void>
  hydrateForAgent: (agentName: string) => Promise<void>
  createPlan: (
    agentName: string,
    overrides: TrainingPlanRequest,
  ) => Promise<TrainingPlanResponse | null>
  executePlan: (agentName: string) => Promise<TrainingResultResponse | null>
  previewPlan: (agentName: string) => Promise<TrainingResultResponse | null>
  updateOverrides: (
    agentName: string,
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
  agentName: string,
): Promise<void> {
  const token = (get().planRequestTokens[agentName] ?? 0) + 1
  set((state) => ({
    planLoading: setMap(state.planLoading, agentName, true),
    planError: setMap(state.planError, agentName, null),
    planRequestTokens: setMap(state.planRequestTokens, agentName, token),
  }))
  const isCurrent = () =>
    get().planRequestTokens[agentName] === token
  try {
    const plan = await getLatestTrainingPlan(agentName)
    if (!isCurrent()) return
    set((state) => ({
      plansByAgent: setMap(state.plansByAgent, agentName, plan),
      planLoading: setMap(state.planLoading, agentName, false),
      planError: setMap(state.planError, agentName, null),
    }))
  } catch (err) {
    if (!isCurrent()) return
    if (isExpectedNotFound(err)) {
      set((state) => ({
        plansByAgent: setMap(state.plansByAgent, agentName, null),
        planLoading: setMap(state.planLoading, agentName, false),
        planError: setMap(state.planError, agentName, null),
      }))
      return
    }
    log.error(
      'fetchPlan failed',
      sanitizeForLog({ agentName, err, message: getErrorMessage(err) }),
    )
    set((state) => ({
      planLoading: setMap(state.planLoading, agentName, false),
      planError: setMap(state.planError, agentName, getErrorMessage(err)),
    }))
  }
}

async function fetchResultImpl(
  set: TrainingSet,
  get: TrainingGet,
  agentName: string,
): Promise<void> {
  const token = (get().resultRequestTokens[agentName] ?? 0) + 1
  set((state) => ({
    resultLoading: setMap(state.resultLoading, agentName, true),
    resultError: setMap(state.resultError, agentName, null),
    resultRequestTokens: setMap(state.resultRequestTokens, agentName, token),
  }))
  const isCurrent = () =>
    get().resultRequestTokens[agentName] === token
  try {
    const result = await getTrainingResult(agentName)
    if (!isCurrent()) return
    set((state) => ({
      resultsByAgent: setMap(state.resultsByAgent, agentName, result),
      resultLoading: setMap(state.resultLoading, agentName, false),
      resultError: setMap(state.resultError, agentName, null),
    }))
  } catch (err) {
    if (!isCurrent()) return
    if (isExpectedNotFound(err)) {
      set((state) => ({
        resultsByAgent: setMap(state.resultsByAgent, agentName, null),
        resultLoading: setMap(state.resultLoading, agentName, false),
        resultError: setMap(state.resultError, agentName, null),
      }))
      return
    }
    const message = getErrorMessage(err)
    log.error(
      'fetchResult failed',
      sanitizeForLog({ agentName, err, message }),
    )
    set((state) => ({
      resultLoading: setMap(state.resultLoading, agentName, false),
      resultError: setMap(state.resultError, agentName, message),
    }))
  }
}

async function createPlanImpl(
  set: TrainingSet,
  agentName: string,
  overrides: TrainingPlanRequest,
): Promise<TrainingPlanResponse | null> {
  try {
    const plan = await createTrainingPlan(agentName, overrides)
    set((state) => ({
      plansByAgent: setMap(state.plansByAgent, agentName, plan),
      resultsByAgent: setMap(state.resultsByAgent, agentName, null),
      planError: setMap(state.planError, agentName, null),
      resultError: setMap(state.resultError, agentName, null),
      planLoading: setMap(state.planLoading, agentName, false),
      resultLoading: setMap(state.resultLoading, agentName, false),
      planRequestTokens: bumpToken(state.planRequestTokens, agentName),
      resultRequestTokens: bumpToken(state.resultRequestTokens, agentName),
    }))
    useToastStore.getState().add({
      variant: 'success',
      title: 'Training plan created',
    })
    return plan
  } catch (err) {
    log.error(
      'createPlan failed',
      sanitizeForLog({ agentName, err, message: getErrorMessage(err) }),
    )
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to create training plan',
      description: getErrorMessage(err),
    })
    return null
  }
}

function buildExecutedPlanPatch(
  state: TrainingState,
  agentName: string,
  result: TrainingResultResponse,
): Partial<TrainingState> {
  const next: Partial<TrainingState> = {
    resultsByAgent: setMap(state.resultsByAgent, agentName, result),
    resultError: setMap(state.resultError, agentName, null),
    planError: setMap(state.planError, agentName, null),
    planLoading: setMap(state.planLoading, agentName, false),
    resultLoading: setMap(state.resultLoading, agentName, false),
    planRequestTokens: bumpToken(state.planRequestTokens, agentName),
    resultRequestTokens: bumpToken(state.resultRequestTokens, agentName),
  }
  const cached = state.plansByAgent[agentName]
  if (cached) {
    next.plansByAgent = setMap(state.plansByAgent, agentName, {
      ...cached,
      status: 'executed',
      executed_at: result.completed_at,
    })
  }
  return next
}

async function executePlanImpl(
  set: TrainingSet,
  agentName: string,
): Promise<TrainingResultResponse | null> {
  try {
    const result = await executeTrainingPlan(agentName)
    set((state) => buildExecutedPlanPatch(state, agentName, result))
    useToastStore.getState().add({
      variant: 'success',
      title: 'Training executed',
    })
    return result
  } catch (err) {
    log.error(
      'executePlan failed',
      sanitizeForLog({ agentName, err, message: getErrorMessage(err) }),
    )
    useToastStore.getState().add({
      variant: 'error',
      title: 'Training execution failed',
      description: getErrorMessage(err),
    })
    return null
  }
}

async function previewPlanImpl(
  agentName: string,
): Promise<TrainingResultResponse | null> {
  try {
    return await previewTrainingPlan(agentName)
  } catch (err) {
    log.error(
      'previewPlan failed',
      sanitizeForLog({ agentName, err, message: getErrorMessage(err) }),
    )
    useToastStore.getState().add({
      variant: 'error',
      title: 'Training preview failed',
      description: getErrorMessage(err),
    })
    return null
  }
}

async function updateOverridesImpl(
  set: TrainingSet,
  agentName: string,
  planId: string,
  data: TrainingOverridesRequest,
): Promise<TrainingPlanResponse | null> {
  try {
    const plan = await updateTrainingOverrides(agentName, planId, data)
    set((state) => ({
      plansByAgent: setMap(state.plansByAgent, agentName, plan),
      planError: setMap(state.planError, agentName, null),
      planLoading: setMap(state.planLoading, agentName, false),
      planRequestTokens: bumpToken(state.planRequestTokens, agentName),
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
        agentName,
        planId,
        err,
        message: getErrorMessage(err),
      }),
    )
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to save overrides',
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

  fetchPlan: (agentName) => fetchPlanImpl(set, get, agentName),
  fetchResult: (agentName) => fetchResultImpl(set, get, agentName),
  hydrateForAgent: async (agentName) => {
    await Promise.all([
      get().fetchPlan(agentName),
      get().fetchResult(agentName),
    ])
  },
  createPlan: (agentName, overrides) =>
    createPlanImpl(set, agentName, overrides),
  executePlan: (agentName) => executePlanImpl(set, agentName),
  previewPlan: (agentName) => previewPlanImpl(agentName),
  updateOverrides: (agentName, planId, data) =>
    updateOverridesImpl(set, agentName, planId, data),
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
  agentName: string,
): AgentLoadingPair {
  return {
    planLoading: state.planLoading[agentName] ?? false,
    resultLoading: state.resultLoading[agentName] ?? false,
  }
}

function pickAgentErrors(
  state: TrainingState,
  agentName: string,
): AgentErrorPair {
  return {
    planError: state.planError[agentName] ?? null,
    resultError: state.resultError[agentName] ?? null,
  }
}

function selectTrainingForAgent(
  state: TrainingState,
  agentName: string,
): TrainingForAgent {
  const loading = pickAgentLoading(state, agentName)
  const errors = pickAgentErrors(state, agentName)
  return {
    plan: state.plansByAgent[agentName] ?? null,
    result: state.resultsByAgent[agentName] ?? null,
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
export function useTrainingForAgent(agentName: string): TrainingForAgent {
  return useTrainingStore(
    useShallow((state) => selectTrainingForAgent(state, agentName)),
  )
}
