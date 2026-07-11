import { http, HttpResponse } from 'msw'

import type {
  editPlan,
  getPlan,
  listPlans,
  requestPlanChanges,
} from '@/api/endpoints/plans'
import type { Plan, PlanItem } from '@/api/types/plans'

import { apiError, emptyPage, paginatedFor, successFor } from './helpers'

function buildItem(overrides: Partial<PlanItem> = {}): PlanItem {
  return {
    id: 'item-1',
    title: 'Scaffold the board',
    description: 'Set up the game grid',
    dependencies: [],
    owner: null,
    acceptance_criteria: [],
    expected_artifacts: [],
    required_skills: [],
    required_tags: [],
    estimated_complexity: 'medium',
    stakes: 'normal',
    ...overrides,
  }
}

function buildPlan(overrides: Partial<Plan> = {}): Plan {
  return {
    id: 'plan-default',
    project: 'beachhead',
    objective_id: 'objective-1',
    parent_task_id: 'task-root',
    items: [buildItem()],
    task_structure: 'sequential',
    coordination_topology: 'auto',
    status: 'pending_review',
    forecast_id: null,
    version: 1,
    created_at: '2026-07-01T10:00:00Z',
    updated_at: '2026-07-01T10:00:00Z',
    ...overrides,
  }
}

export const plansHandlers = [
  http.get('/api/v1/plans', () =>
    HttpResponse.json(paginatedFor<typeof listPlans>(emptyPage<Plan>())),
  ),
  http.get('/api/v1/plans/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getPlan>(buildPlan({ id: String(params['id']) })),
    ),
  ),
  http.patch('/api/v1/plans/:id', async ({ params, request }) => {
    const body = (await request.json()) as { items?: readonly unknown[] }
    if (!body.items || body.items.length === 0) {
      return HttpResponse.json(apiError("Field 'items' must be non-empty"), {
        status: 422,
      })
    }
    return HttpResponse.json(
      successFor<typeof editPlan>(
        buildPlan({ id: String(params['id']), version: 2 }),
      ),
    )
  }),
  http.post('/api/v1/plans/:id/request-changes', async ({ params, request }) => {
    const body = (await request.json()) as { note?: string }
    if (!body.note) {
      return HttpResponse.json(apiError("Field 'note' is required"), {
        status: 422,
      })
    }
    return HttpResponse.json(
      successFor<typeof requestPlanChanges>(
        buildPlan({ id: String(params['id']), status: 'draft' }),
      ),
    )
  }),
]
