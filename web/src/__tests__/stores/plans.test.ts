import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { listPlans } from '@/api/endpoints/plans'
import { apiError, apiSuccess, paginatedFor } from '@/mocks/handlers'
import { usePlansStore } from '@/stores/plans'
import { server } from '@/test-setup'

import { makePlan } from '../helpers/factories'

describe('usePlansStore', () => {
  beforeEach(() => {
    usePlansStore.getState().reset()
  })

  describe('fetchPlans', () => {
    it('populates plans on success', async () => {
      const plan = makePlan('plan-1')
      server.use(
        http.get('/api/v1/plans', () =>
          HttpResponse.json(
            paginatedFor<typeof listPlans>({
              data: [plan],
              limit: 200,
              nextCursor: null,
              hasMore: false,
              pagination: { limit: 200, next_cursor: null, has_more: false },
            }),
          ),
        ),
      )
      await usePlansStore.getState().fetchPlans()
      expect(usePlansStore.getState().plans).toEqual([plan])
      expect(usePlansStore.getState().listError).toBeNull()
    })

    it('records an error message on failure', async () => {
      server.use(
        http.get('/api/v1/plans', () =>
          HttpResponse.json(apiError('boom'), { status: 500 }),
        ),
      )
      await usePlansStore.getState().fetchPlans()
      expect(usePlansStore.getState().listError).not.toBeNull()
    })
  })

  describe('fetchPlanDetail', () => {
    it('sets the selected plan', async () => {
      const plan = makePlan('plan-1')
      server.use(
        http.get('/api/v1/plans/:id', () => HttpResponse.json(apiSuccess(plan))),
      )
      await usePlansStore.getState().fetchPlanDetail('plan-1')
      expect(usePlansStore.getState().selectedPlan).toEqual(plan)
    })

    it('records an error when the plan is gone', async () => {
      server.use(
        http.get('/api/v1/plans/:id', () =>
          HttpResponse.json(apiError('gone'), { status: 404 }),
        ),
      )
      await usePlansStore.getState().fetchPlanDetail('plan-1')
      expect(usePlansStore.getState().selectedPlan).toBeNull()
      expect(usePlansStore.getState().detailError).not.toBeNull()
    })
  })

  describe('editPlan', () => {
    it('upserts the revised plan and returns it', async () => {
      const original = makePlan('plan-1', { version: 1 })
      const revised = makePlan('plan-1', { version: 2 })
      usePlansStore.setState({ plans: [original], selectedPlan: original })
      server.use(
        http.patch('/api/v1/plans/:id', () => HttpResponse.json(apiSuccess(revised))),
      )
      const result = await usePlansStore.getState().editPlan('plan-1', {
        items: [
          {
            id: 'item-1',
            title: 'X',
            description: 'Y',
            dependencies: [],
            acceptance_criteria: ['done'],
            expected_artifacts: [],
            required_skills: [],
            required_tags: [],
            options: [],
            satisfies: [],
          },
        ],
      })
      expect(result).toEqual(revised)
      expect(usePlansStore.getState().plans[0]?.version).toBe(2)
      expect(usePlansStore.getState().selectedPlan?.version).toBe(2)
    })

    it('returns null on failure', async () => {
      usePlansStore.setState({ plans: [makePlan('plan-1')] })
      server.use(
        http.patch('/api/v1/plans/:id', () =>
          HttpResponse.json(apiError('bad'), { status: 422 }),
        ),
      )
      const result = await usePlansStore.getState().editPlan('plan-1', { items: [] })
      expect(result).toBeNull()
    })
  })

  describe('deletePlan', () => {
    it('drops the plan from the list and the open detail', async () => {
      const plan = makePlan('plan-1')
      usePlansStore.setState({
        plans: [plan, makePlan('plan-2')],
        selectedPlan: plan,
      })
      server.use(
        http.delete('/api/v1/plans/:id', () => new HttpResponse(null, { status: 204 })),
      )

      const removed = await usePlansStore.getState().deletePlan('plan-1')

      expect(removed).toBe(true)
      expect(usePlansStore.getState().plans.map((p) => p.id)).toEqual(['plan-2'])
      expect(usePlansStore.getState().selectedPlan).toBeNull()
    })

    it('keeps the plan when the API refuses the delete', async () => {
      const plan = makePlan('plan-1', { status: 'executing' })
      usePlansStore.setState({ plans: [plan], selectedPlan: plan })
      server.use(
        http.delete('/api/v1/plans/:id', () =>
          HttpResponse.json(apiError('Plan is dispatched'), { status: 409 }),
        ),
      )

      const removed = await usePlansStore.getState().deletePlan('plan-1')

      expect(removed).toBe(false)
      expect(usePlansStore.getState().plans).toEqual([plan])
      expect(usePlansStore.getState().selectedPlan).toEqual(plan)
    })
  })

  describe('requestPlanChanges', () => {
    it('drafts the plan and returns it', async () => {
      const drafted = makePlan('plan-1', { status: 'draft' })
      usePlansStore.setState({
        plans: [makePlan('plan-1')],
        selectedPlan: makePlan('plan-1'),
      })
      server.use(
        http.post('/api/v1/plans/:id/request-changes', () =>
          HttpResponse.json(apiSuccess(drafted)),
        ),
      )
      const result = await usePlansStore
        .getState()
        .requestPlanChanges('plan-1', 'please revise')
      expect(result?.status).toBe('draft')
      expect(usePlansStore.getState().selectedPlan?.status).toBe('draft')
    })
  })

  describe('updateFromWsEvent', () => {
    it('refreshes the open plan when a replan retires it', async () => {
      // The event names the successor, so `plan_id` is an id this viewer
      // does not hold; `supersedes` is the only one that reaches them.
      const retired = makePlan('plan-1')
      const superseded = makePlan('plan-1', { status: 'superseded' })
      usePlansStore.setState({ plans: [retired], selectedPlan: retired })
      server.use(
        http.get('/api/v1/plans/plan-1', () => HttpResponse.json(apiSuccess(superseded))),
        http.get('/api/v1/plans', () =>
          HttpResponse.json(
            paginatedFor<typeof listPlans>({
              data: [superseded],
              limit: 200,
              nextCursor: null,
              hasMore: false,
              pagination: { limit: 200, next_cursor: null, has_more: false },
            }),
          ),
        ),
      )

      usePlansStore.getState().updateFromWsEvent({
        event_type: 'plan.updated',
        channel: 'plans',
        timestamp: '2026-01-01T00:00:00Z',
        payload: { plan_id: 'plan-2', version: 1, status: 'draft', supersedes: 'plan-1' },
      })
      await vi.waitFor(() =>
        expect(usePlansStore.getState().selectedPlan?.status).toBe('superseded'),
      )
    })
  })
})
