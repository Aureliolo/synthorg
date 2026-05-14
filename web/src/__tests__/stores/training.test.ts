import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as trainingApi from '@/api/endpoints/training'
import type {
  TrainingPlanResponse,
  TrainingResultResponse,
} from '@/api/endpoints/training'
import { useTrainingStore } from '@/stores/training'
import { useToastStore } from '@/stores/toast'

describe('useTrainingStore', () => {
  function mockPlan(
    overrides: Partial<TrainingPlanResponse> = {},
  ): TrainingPlanResponse {
    return {
      id: 'plan-1',
      new_agent_id: 'agent-1',
      new_agent_role: 'engineer',
      source_selector_type: 'seniority',
      enabled_content_types: ['procedural', 'semantic', 'tool_patterns'],
      curation_strategy_type: 'default',
      volume_caps: [],
      override_sources: [],
      skip_training: false,
      require_review: true,
      status: 'pending',
      created_at: '2026-04-01T00:00:00Z',
      executed_at: null,
      ...overrides,
    }
  }

  function mockResult(
    overrides: Partial<TrainingResultResponse> = {},
  ): TrainingResultResponse {
    return {
      id: 'res-1',
      plan_id: 'plan-1',
      new_agent_id: 'agent-1',
      source_agents_used: ['source-1'],
      items_extracted: [['procedural', 3]],
      items_after_curation: [['procedural', 3]],
      items_after_guards: [['procedural', 3]],
      items_stored: [['procedural', 3]],
      approval_item_id: null,
      pending_approvals: [],
      review_pending: false,
      errors: [],
      started_at: '2026-04-01T00:00:00Z',
      completed_at: '2026-04-01T00:05:00Z',
      ...overrides,
    }
  }

  /**
   * Snapshot of the initial store state. Used by ``beforeEach`` to
   * restore every caches + flag key (not just the subset the test
   * cares about) so leftover mutations from a prior test cannot leak.
   */
  const initialStoreState = useTrainingStore.getState()

  beforeEach(() => {
    useTrainingStore.setState(initialStoreState, true)
    useToastStore.setState({ toasts: [] })
    vi.restoreAllMocks()
  })

  it('fetchResult stores the result and clears resultLoading/resultError', async () => {
    const result = mockResult()
    const spy = vi
      .spyOn(trainingApi, 'getTrainingResult')
      .mockResolvedValueOnce(result)

    await useTrainingStore.getState().fetchResult('agent-1')

    expect(spy).toHaveBeenCalledWith('agent-1')
    const state = useTrainingStore.getState()
    expect(state.resultsByAgent['agent-1']).toEqual(result)
    expect(state.resultLoading['agent-1']).toBe(false)
    expect(state.resultError['agent-1']).toBeNull()
  })

  it('fetchResult sets resultError when the API rejects (plan slot untouched)', async () => {
    vi.spyOn(trainingApi, 'getTrainingResult').mockRejectedValueOnce(
      new Error('offline'),
    )

    await useTrainingStore.getState().fetchResult('agent-1')

    const state = useTrainingStore.getState()
    expect(state.resultsByAgent).not.toHaveProperty('agent-1')
    expect(state.resultLoading['agent-1']).toBe(false)
    expect(state.resultError['agent-1']).toContain('offline')
    // Plan-side state must not be mutated by a result-side failure.
    expect(state.planError).not.toHaveProperty('agent-1')
    expect(state.planLoading).not.toHaveProperty('agent-1')
  })

  it('fetchResult treats a 404 as an empty cache (no error banner)', async () => {
    // Shape mirrors `axios.isAxiosError`: the helper checks the
    // `isAxiosError` flag, not just `response`. Without the flag, the
    // store treats the rejection as a generic error and shows a toast.
    const notFound = Object.assign(new Error('Not Found'), {
      isAxiosError: true,
      response: { status: 404 },
    })
    vi.spyOn(trainingApi, 'getTrainingResult').mockRejectedValueOnce(notFound)

    await useTrainingStore.getState().fetchResult('agent-1')

    const state = useTrainingStore.getState()
    expect(state.resultsByAgent['agent-1']).toBeNull()
    expect(state.resultError['agent-1']).toBeNull()
    expect(state.resultLoading['agent-1']).toBe(false)
  })

  it('fetchPlan stores the plan on success', async () => {
    const plan = mockPlan()
    const spy = vi
      .spyOn(trainingApi, 'getLatestTrainingPlan')
      .mockResolvedValueOnce(plan)

    await useTrainingStore.getState().fetchPlan('agent-1')

    expect(spy).toHaveBeenCalledWith('agent-1')
    const state = useTrainingStore.getState()
    expect(state.plansByAgent['agent-1']).toEqual(plan)
    expect(state.planLoading['agent-1']).toBe(false)
    expect(state.planError['agent-1']).toBeNull()
  })

  it('fetchPlan treats a 404 as an empty cache', async () => {
    // Shape mirrors `axios.isAxiosError`: the helper checks the
    // `isAxiosError` flag, not just `response`. Without the flag, the
    // store treats the rejection as a generic error and shows a toast.
    const notFound = Object.assign(new Error('Not Found'), {
      isAxiosError: true,
      response: { status: 404 },
    })
    vi.spyOn(trainingApi, 'getLatestTrainingPlan').mockRejectedValueOnce(
      notFound,
    )

    await useTrainingStore.getState().fetchPlan('agent-1')

    const state = useTrainingStore.getState()
    expect(state.plansByAgent['agent-1']).toBeNull()
    expect(state.planError['agent-1']).toBeNull()
    expect(state.planLoading['agent-1']).toBe(false)
  })

  it('fetchPlan failure does not clobber an in-flight fetchResult', async () => {
    // Race guard: simulate a slow plan fetch that fails AFTER a
    // separate result fetch has already completed successfully. With
    // shared maps this would have wiped ``resultLoading`` back to
    // ``false`` (already false) and -- worse -- left a stale plan
    // error banner on the result side. With split maps the two
    // slots stay independent.
    const result = mockResult()
    vi.spyOn(trainingApi, 'getTrainingResult').mockResolvedValueOnce(result)
    vi.spyOn(trainingApi, 'getLatestTrainingPlan').mockRejectedValueOnce(
      new Error('plan outage'),
    )

    await useTrainingStore.getState().fetchResult('agent-1')
    await useTrainingStore.getState().fetchPlan('agent-1')

    const state = useTrainingStore.getState()
    expect(state.resultsByAgent['agent-1']).toEqual(result)
    expect(state.resultError['agent-1']).toBeNull()
    expect(state.planError['agent-1']).toContain('plan outage')
  })

  it('hydrateForAgent fetches plan and result in parallel', async () => {
    const plan = mockPlan({ status: 'executed' })
    const result = mockResult()
    const planSpy = vi
      .spyOn(trainingApi, 'getLatestTrainingPlan')
      .mockResolvedValueOnce(plan)
    const resultSpy = vi
      .spyOn(trainingApi, 'getTrainingResult')
      .mockResolvedValueOnce(result)

    await useTrainingStore.getState().hydrateForAgent('agent-1')

    expect(planSpy).toHaveBeenCalledWith('agent-1')
    expect(resultSpy).toHaveBeenCalledWith('agent-1')
    const state = useTrainingStore.getState()
    expect(state.plansByAgent['agent-1']).toEqual(plan)
    expect(state.resultsByAgent['agent-1']).toEqual(result)
  })

  it('createPlan updates the store and emits a success toast', async () => {
    const plan = mockPlan()
    const request = {
      override_sources: [],
      skip_training: false,
      require_review: true,
    }
    const spy = vi
      .spyOn(trainingApi, 'createTrainingPlan')
      .mockResolvedValueOnce(plan)

    const returned = await useTrainingStore
      .getState()
      .createPlan('agent-1', request)

    expect(spy).toHaveBeenCalledWith('agent-1', request)
    expect(returned).toEqual(plan)
    expect(useTrainingStore.getState().plansByAgent['agent-1']).toEqual(plan)
    const toasts = useToastStore.getState().toasts
    expect(toasts).toHaveLength(1)
    expect(toasts[0]?.variant).toBe('success')
  })

  it('createPlan returns null and emits an error toast on failure', async () => {
    vi.spyOn(trainingApi, 'createTrainingPlan').mockRejectedValueOnce(
      new Error('server exploded'),
    )

    const returned = await useTrainingStore.getState().createPlan('agent-1', {
      override_sources: [],
      skip_training: false,
      require_review: true,
    })

    expect(returned).toBeNull()
    const toasts = useToastStore.getState().toasts
    expect(toasts[0]?.variant).toBe('error')
    expect(toasts[0]?.description).toContain('server exploded')
  })

  it('executePlan stores the result and marks the cached plan as executed', async () => {
    const plan = mockPlan({ status: 'pending' })
    const result = mockResult({ completed_at: '2026-04-01T01:00:00Z' })
    useTrainingStore.setState((state) => ({
      plansByAgent: { ...state.plansByAgent, 'agent-1': plan },
    }))
    const spy = vi
      .spyOn(trainingApi, 'executeTrainingPlan')
      .mockResolvedValueOnce(result)

    const returned = await useTrainingStore.getState().executePlan('agent-1')

    expect(spy).toHaveBeenCalledWith('agent-1')
    expect(returned).toEqual(result)
    const state = useTrainingStore.getState()
    expect(state.resultsByAgent['agent-1']).toEqual(result)
    expect(state.plansByAgent['agent-1']?.status).toBe('executed')
    expect(state.plansByAgent['agent-1']?.executed_at).toBe(
      '2026-04-01T01:00:00Z',
    )
  })

  it('executePlan without a cached plan still stores the result', async () => {
    const result = mockResult()
    vi.spyOn(trainingApi, 'executeTrainingPlan').mockResolvedValueOnce(result)

    await useTrainingStore.getState().executePlan('agent-1')

    const state = useTrainingStore.getState()
    expect(state.resultsByAgent['agent-1']).toEqual(result)
    expect(state.plansByAgent).not.toHaveProperty('agent-1')
  })

  it('mutation success clears stale read-error banners', async () => {
    // Arrange: seed both error slots as if an earlier read had failed.
    useTrainingStore.setState((state) => ({
      planError: { ...state.planError, 'agent-1': 'stale plan boom' },
      resultError: { ...state.resultError, 'agent-1': 'stale result boom' },
    }))

    const result = mockResult({ completed_at: '2026-04-01T02:00:00Z' })
    vi.spyOn(trainingApi, 'executeTrainingPlan').mockResolvedValueOnce(result)

    await useTrainingStore.getState().executePlan('agent-1')

    const state = useTrainingStore.getState()
    expect(state.resultError['agent-1']).toBeNull()
    expect(state.planError['agent-1']).toBeNull()
  })

  it('updateOverrides replaces the cached plan on success', async () => {
    const updated = mockPlan({ status: 'executed' })
    const spy = vi
      .spyOn(trainingApi, 'updateTrainingOverrides')
      .mockResolvedValueOnce(updated)

    const returned = await useTrainingStore
      .getState()
      .updateOverrides('agent-1', 'plan-1', { override_sources: ['s2'] })

    expect(spy).toHaveBeenCalledWith('agent-1', 'plan-1', {
      override_sources: ['s2'],
    })
    expect(returned).toEqual(updated)
    expect(useTrainingStore.getState().plansByAgent['agent-1']).toEqual(updated)
  })
})
