import { http, HttpResponse } from 'msw'

import type {
  editPlan,
  getPlan,
  getPlanEvaluation,
  getPlanTransitions,
  listPlans,
  replanPlan,
  requestPlanChanges,
} from '@/api/endpoints/plans'
import type { Plan, PlanItem } from '@/api/types/plans'

import { apiError, emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'

function buildItem(overrides: Partial<PlanItem> = {}): PlanItem {
  return {
    id: 'item-1',
    title: 'Scaffold the board',
    description: 'Set up the game grid',
    dependencies: [],
    owner: null,
    owner_name: null,
    acceptance_criteria: ['board grid renders'],
    // Non-empty: a work item declaring no deliverable is rejected by the
    // backend, so an empty default would model a state the API cannot return.
    expected_artifacts: ['web/src/game/board.tsx'],
    required_skills: [],
    required_tags: [],
    estimated_complexity: 'medium',
    stakes: 'normal',
    kind: 'work',
    options: [],
    chosen_option_id: null,
    satisfies: [],
    ...overrides,
  }
}

function buildPlan(overrides: Partial<Plan> = {}): Plan {
  return {
    id: 'plan-default',
    project: 'beachhead',
    project_name: 'Beachhead',
    objective_id: 'objective-1',
    objective_title: 'Ship the Tetris game',
    parent_task_id: 'task-root',
    items: [buildItem()],
    task_structure: 'sequential',
    coordination_topology: 'auto',
    status: 'pending_review',
    failure_reason: null,
    forecast_id: null,
    review: null,
    // Null is the ordinary case for both: the configured planner produced the
    // items, and a real panel reviewed them, so neither has anything to say.
    planning_strategy: null,
    review_absent_reason: null,
    open_questions: [],
    assumptions: [],
    objective_criteria: [],
    version_history: [],
    replan_generation: 0,
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
  // Unjudged by default: most plans on screen have never reached EVALUATING,
  // and a canned verdict would render the panel in every unrelated test.
  http.get('/api/v1/plans/:id/evaluation', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getPlanEvaluation>({
        plan_id: String(params['id']),
        attempts: [],
      }),
    ),
  ),
  // One row by default: every plan on screen has been through at least the
  // move that made it reviewable, so an empty ledger would model a state the
  // backend only produces when the ledger itself failed to record.
  http.get('/api/v1/plans/:id/transitions', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getPlanTransitions>([
        {
          id: 'transition-1',
          entity_kind: 'plan',
          entity_id: String(params['id']),
          from_status: 'planning',
          to_status: 'pending_review',
          requested_by: null,
          reason: null,
          entity_version: 2,
          occurred_at: '2026-07-01T10:00:00Z',
        },
      ]),
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
  http.delete('/api/v1/plans/:id', () => HttpResponse.json(voidSuccess())),
  http.post('/api/v1/plans/:id/replan', async ({ request }) => {
    // Validate rather than cast: a null body would throw on property access,
    // and a bare string would satisfy a `.length` check the backend rejects.
    const body: unknown = await request.json().catch(() => null)
    const items =
      body !== null && typeof body === 'object' && 'items' in body
        ? (body as { items?: unknown }).items
        : undefined
    if (!Array.isArray(items) || items.length === 0) {
      return HttpResponse.json(apiError("Field 'items' must be non-empty"), {
        status: 422,
      })
    }
    // A re-plan returns the successor, a new plan entity awaiting review.
    return HttpResponse.json(
      successFor<typeof replanPlan>(
        buildPlan({ id: 'plan-successor', status: 'pending_review' }),
      ),
      { status: 201 },
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
