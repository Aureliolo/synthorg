/**
 * Data controller for the Model Tier Assignment panel. Hydrates the
 * effective tier map and classifier model from the backend on mount and
 * writes every override / recommendation / classifier change straight back
 * through the tier-assignment REST API (Pure API Consumer: no client-side
 * persistence, the backend is the sole source of truth).
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  applyCapabilityRecommendation,
  getTierClassifierModel,
  listCapabilityAssignments,
  recommendAllTiers,
  recommendCapabilityLevel,
  setTierClassifierModel,
  setTierOverride,
} from '@/api/endpoints/providers'
import type {
  ClassifierModelDTO,
  CapabilityAssignmentDTO,
  CapabilityRecommendationDTO,
} from '@/api/types/providers'
import { useToastStore } from '@/stores/toast'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { getErrorMessage } from '@/utils/errors'
import { createCancellationToken, type CancellationToken } from '@/utils/cancellation'

const log = createLogger('CapabilityLevelAssignments')

/** Stable identity for one ``(provider, model_id)`` pair. */
export function tierRowKey(provider: string, modelId: string): string {
  return `${provider} ${modelId}`
}

export interface CapabilityAssignmentsState {
  assignments: readonly CapabilityAssignmentDTO[]
  classifier: ClassifierModelDTO | null
  recommendations: Readonly<Record<string, CapabilityRecommendationDTO>>
  loading: boolean
  error: string | null
  recommendingKeys: ReadonlySet<string>
  savingKeys: ReadonlySet<string>
  recommendingAll: boolean
}

export interface CapabilityAssignmentsController {
  state: CapabilityAssignmentsState
  load: () => void
  setOverride: (provider: string, modelId: string, tier: CapabilityAssignmentDTO['tier'] | null) => void
  recommendOne: (provider: string, modelId: string) => void
  recommendAll: () => void
  applyRecommendation: (rec: CapabilityRecommendationDTO) => void
  setClassifier: (provider: string, modelId: string) => void
  setRecommenderEnabled: (enabled: boolean) => void
}

const INITIAL: CapabilityAssignmentsState = {
  assignments: [],
  classifier: null,
  recommendations: {},
  loading: true,
  error: null,
  recommendingKeys: new Set(),
  savingKeys: new Set(),
  recommendingAll: false,
}

/** True when a classifier model has been selected (both fields non-empty). */
export function hasClassifierModel(classifier: ClassifierModelDTO | null): boolean {
  return classifier !== null && classifier.provider !== '' && classifier.model_id !== ''
}

/** True when the LLM recommender can run: a model is set and the opt-in is on. */
export function canRecommend(classifier: ClassifierModelDTO | null): boolean {
  return hasClassifierModel(classifier) && (classifier?.enabled ?? false)
}

function withKey(
  set: (updater: (prev: CapabilityAssignmentsState) => CapabilityAssignmentsState) => void,
  field: 'recommendingKeys' | 'savingKeys',
  key: string,
  active: boolean,
): void {
  set((prev) => {
    const next = new Set(prev[field])
    if (active) next.add(key)
    else next.delete(key)
    return { ...prev, [field]: next }
  })
}

function indexRecommendations(
  offers: readonly CapabilityRecommendationDTO[],
): Record<string, CapabilityRecommendationDTO> {
  const out: Record<string, CapabilityRecommendationDTO> = {}
  for (const offer of offers) {
    out[tierRowKey(offer.provider, offer.model_id)] = offer
  }
  return out
}

function useLoad(
  setState: (u: (p: CapabilityAssignmentsState) => CapabilityAssignmentsState) => void,
): (token: CancellationToken) => void {
  return useCallback((token: CancellationToken) => {
    setState((prev) => ({ ...prev, loading: true, error: null }))
    void Promise.all([listCapabilityAssignments(), getTierClassifierModel()])
      .then(([assignmentsResponse, classifier]) => {
        if (token.cancelled()) return
        setState((prev) => ({
          ...prev,
          assignments: assignmentsResponse.assignments,
          classifier,
          loading: false,
          error: null,
        }))
      })
      .catch((err: unknown) => {
        if (token.cancelled()) return
        const message = getErrorMessage(err)
        log.error('load tier assignments failed', { error: sanitizeForLog(message) })
        setState((prev) => ({ ...prev, loading: false, error: message }))
      })
  }, [setState])
}

function useOverride(
  setState: (u: (p: CapabilityAssignmentsState) => CapabilityAssignmentsState) => void,
): CapabilityAssignmentsController['setOverride'] {
  return useCallback(
    (provider, modelId, tier) => {
      const key = tierRowKey(provider, modelId)
      withKey(setState, 'savingKeys', key, true)
      void setTierOverride(provider, modelId, { tier, reason: 'operator override' })
        .then((response) => {
          setState((prev) => ({ ...prev, assignments: response.assignments }))
          useToastStore.getState().add({
            variant: 'success',
            title: tier === null ? 'Reverted to heuristic tier' : 'Tier override saved',
          })
        })
        .catch((err: unknown) => {
          const message = getErrorMessage(err)
          log.error('set tier override failed', { error: sanitizeForLog(message) })
          useToastStore
            .getState()
            .add({ variant: 'error', title: 'Could not save tier override', description: message })
        })
        .finally(() => withKey(setState, 'savingKeys', key, false))
    },
    [setState],
  )
}

function useRecommendOne(
  setState: (u: (p: CapabilityAssignmentsState) => CapabilityAssignmentsState) => void,
): CapabilityAssignmentsController['recommendOne'] {
  return useCallback(
    (provider, modelId) => {
      const key = tierRowKey(provider, modelId)
      withKey(setState, 'recommendingKeys', key, true)
      void recommendCapabilityLevel(provider, modelId)
        .then((response) => {
          setState((prev) => ({
            ...prev,
            recommendations: { ...prev.recommendations, ...indexRecommendations(response.recommendations) },
          }))
        })
        .catch((err: unknown) => {
          const message = getErrorMessage(err)
          log.error('recommend tier failed', { error: sanitizeForLog(message) })
          useToastStore
            .getState()
            .add({ variant: 'error', title: 'Could not recommend a tier', description: message })
        })
        .finally(() => withKey(setState, 'recommendingKeys', key, false))
    },
    [setState],
  )
}

function useRecommendAll(
  setState: (u: (p: CapabilityAssignmentsState) => CapabilityAssignmentsState) => void,
): CapabilityAssignmentsController['recommendAll'] {
  return useCallback(() => {
    setState((prev) => ({ ...prev, recommendingAll: true }))
    void recommendAllTiers()
      .then((response) => {
        setState((prev) => ({
          ...prev,
          recommendations: indexRecommendations(response.recommendations),
        }))
        useToastStore.getState().add({
          variant: 'success',
          title: 'Fresh tier recommendations ready',
        })
      })
      .catch((err: unknown) => {
        const message = getErrorMessage(err)
        log.error('recommend all tiers failed', { error: sanitizeForLog(message) })
        useToastStore
          .getState()
          .add({ variant: 'error', title: 'Could not recommend tiers', description: message })
      })
      .finally(() => setState((prev) => ({ ...prev, recommendingAll: false })))
  }, [setState])
}

function useApply(
  setState: (u: (p: CapabilityAssignmentsState) => CapabilityAssignmentsState) => void,
): CapabilityAssignmentsController['applyRecommendation'] {
  return useCallback(
    (rec) => {
      const key = tierRowKey(rec.provider, rec.model_id)
      withKey(setState, 'savingKeys', key, true)
      void applyCapabilityRecommendation({
        provider: rec.provider,
        model_id: rec.model_id,
        tier: rec.tier,
        rationale: rec.rationale,
      })
        .then((response) => {
          setState((prev) => {
            const recommendations = Object.fromEntries(
              Object.entries(prev.recommendations).filter(([k]) => k !== key),
            )
            return { ...prev, assignments: response.assignments, recommendations }
          })
          useToastStore.getState().add({ variant: 'success', title: 'Recommendation applied' })
        })
        .catch((err: unknown) => {
          const message = getErrorMessage(err)
          log.error('apply recommendation failed', { error: sanitizeForLog(message) })
          useToastStore
            .getState()
            .add({ variant: 'error', title: 'Could not apply recommendation', description: message })
        })
        .finally(() => withKey(setState, 'savingKeys', key, false))
    },
    [setState],
  )
}

function saveClassifier(
  setState: (u: (p: CapabilityAssignmentsState) => CapabilityAssignmentsState) => void,
  next: ClassifierModelDTO,
  successTitle: string,
): void {
  void setTierClassifierModel(next)
    .then((classifier) => {
      setState((prev) => ({ ...prev, classifier }))
      useToastStore.getState().add({ variant: 'success', title: successTitle })
    })
    .catch((err: unknown) => {
      const message = getErrorMessage(err)
      log.error('set classifier model failed', { error: sanitizeForLog(message) })
      useToastStore
        .getState()
        .add({ variant: 'error', title: 'Could not update classifier', description: message })
    })
}

function useClassifierActions(
  setState: (u: (p: CapabilityAssignmentsState) => CapabilityAssignmentsState) => void,
  classifierRef: { readonly current: ClassifierModelDTO | null },
): Pick<CapabilityAssignmentsController, 'setClassifier' | 'setRecommenderEnabled'> {
  const setClassifier = useCallback<CapabilityAssignmentsController['setClassifier']>(
    (provider, modelId) => {
      // Preserve the current opt-in when only the model changes.
      const enabled = classifierRef.current?.enabled ?? false
      saveClassifier(
        setState,
        { provider, model_id: modelId, enabled },
        'Classifier model set',
      )
    },
    [setState, classifierRef],
  )
  const setRecommenderEnabled = useCallback<
    CapabilityAssignmentsController['setRecommenderEnabled']
  >(
    (enabled) => {
      const current = classifierRef.current
      saveClassifier(
        setState,
        {
          provider: current?.provider ?? '',
          model_id: current?.model_id ?? '',
          enabled,
        },
        enabled ? 'LLM recommender enabled' : 'LLM recommender disabled',
      )
    },
    [setState, classifierRef],
  )
  return { setClassifier, setRecommenderEnabled }
}

export function useModelCapabilities(): CapabilityAssignmentsController {
  const [state, setState] = useState<CapabilityAssignmentsState>(INITIAL)

  // Latest classifier for the classifier actions, without re-creating the
  // stable callbacks each time the model changes.
  const classifierRef = useRef(state.classifier)
  classifierRef.current = state.classifier

  const loadWith = useLoad(setState)
  const load = useCallback(() => {
    loadWith(createCancellationToken())
  }, [loadWith])
  const setOverride = useOverride(setState)
  const recommendOne = useRecommendOne(setState)
  const recommendAll = useRecommendAll(setState)
  const applyRecommendation = useApply(setState)
  const { setClassifier, setRecommenderEnabled } = useClassifierActions(
    setState,
    classifierRef,
  )

  useEffect(() => {
    // A fresh token per effect run: its cleanup cancels only this run's load,
    // so a re-run never reuses (and is never silenced by) a cancelled token.
    const token = createCancellationToken()
    void Promise.resolve().then(() => {
      loadWith(token)
    })
    return () => token.cancel()
  }, [loadWith])

  return {
    state,
    load,
    setOverride,
    recommendOne,
    recommendAll,
    applyRecommendation,
    setClassifier,
    setRecommenderEnabled,
  }
}
