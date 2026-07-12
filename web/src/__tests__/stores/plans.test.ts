import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import type { listPlans } from '@/api/endpoints/plans'
import { apiError, apiSuccess, paginatedFor } from '@/mocks/handlers'
import { usePlansStore } from '@/stores/plans'
import { server } from '@/test-setup'

import { makePlan, makeTask } from '../helpers/factories'

function resetStore(): void {
  usePlansStore.setState({
    plans: [],
    listLoading: false,
    listError: null,
    statusFilter: null,
    selectedPlan: null,
    detailLoading: false,
    detailError: null,
    parentTaskTitle: null,
  })
}

describe('usePlansStore', () => {
  beforeEach(resetStore)

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

    it('resolves the human headline from the parent objective task', async () => {
      const plan = makePlan('plan-1', { parent_task_id: 'task-root' })
      server.use(
        http.get('/api/v1/plans/:id', () => HttpResponse.json(apiSuccess(plan))),
        http.get('/api/v1/tasks/:id', () =>
          HttpResponse.json(
            apiSuccess(makeTask('task-root', 'Ship the Tetris game')),
          ),
        ),
      )
      await usePlansStore.getState().fetchPlanDetail('plan-1')
      expect(usePlansStore.getState().parentTaskTitle).toBe('Ship the Tetris game')
    })

    it('leaves the headline unresolved when the parent task is gone', async () => {
      const plan = makePlan('plan-1')
      server.use(
        http.get('/api/v1/plans/:id', () => HttpResponse.json(apiSuccess(plan))),
        http.get('/api/v1/tasks/:id', () =>
          HttpResponse.json(apiError('gone'), { status: 404 }),
        ),
      )
      await usePlansStore.getState().fetchPlanDetail('plan-1')
      expect(usePlansStore.getState().selectedPlan).toEqual(plan)
      expect(usePlansStore.getState().parentTaskTitle).toBeNull()
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
            acceptance_criteria: [],
            expected_artifacts: [],
            required_skills: [],
            required_tags: [],
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

  describe('requestPlanChanges', () => {
    it('drafts the plan and returns it', async () => {
      const drafted = makePlan('plan-1', { status: 'draft' })
      usePlansStore.setState({ plans: [makePlan('plan-1')], selectedPlan: makePlan('plan-1') })
      server.use(
        http.post('/api/v1/plans/:id/request-changes', () =>
          HttpResponse.json(apiSuccess(drafted)),
        ),
      )
      const result = await usePlansStore.getState().requestPlanChanges('plan-1', 'please revise')
      expect(result?.status).toBe('draft')
      expect(usePlansStore.getState().selectedPlan?.status).toBe('draft')
    })
  })
})
