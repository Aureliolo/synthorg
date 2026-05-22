import { http, HttpResponse } from 'msw'
import type {
  cancelTask,
  createTask,
  getTask,
  listTasks,
  transitionTask,
  updateTask,
} from '@/api/endpoints/tasks'
import type { Priority } from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'
import { apiError, emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'

export function buildTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-default',
    title: 'Default task',
    description: '',
    type: 'development',
    status: 'created',
    priority: 'medium',
    project: 'project-default',
    created_by: 'agent-default',
    assigned_to: null,
    reviewers: [],
    dependencies: [],
    artifacts_expected: [],
    acceptance_criteria: [],
    estimated_complexity: 'medium',
    stakes: 'normal',
    budget_limit: 10,
    deadline: null,
    max_retries: 3,
    parent_task_id: null,
    delegation_chain: [],
    task_structure: null,
    coordination_topology: 'auto',
    middleware_override: null,
    source: null,
    metadata: {},
    hard_ceiling: null,
    forecast_id: null,
    version: 1,
    ...overrides,
  }
}

export const tasksHandlers = [
  http.get('/api/v1/tasks', () =>
    HttpResponse.json(paginatedFor<typeof listTasks>(emptyPage<Task>())),
  ),
  http.get('/api/v1/tasks/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getTask>(buildTask({ id: String(params.id) })),
    ),
  ),
  http.post('/api/v1/tasks', async ({ request }) => {
    const body = (await request.json()) as {
      title?: string
      description?: string
      type?: Task['type']
      priority?: Priority
      project?: string
      created_by?: string
    }
    if (!body.title || !body.project || !body.created_by || !body.type) {
      return HttpResponse.json(apiError('Missing required fields'), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof createTask>({
        correlation_id: crypto.randomUUID(),
        title: body.title,
        project: body.project,
        status: 'submitted',
      }),
      { status: 202 },
    )
  }),
  http.patch('/api/v1/tasks/:id', async ({ params, request }) => {
    const body = (await request.json()) as Partial<Task>
    return HttpResponse.json(
      successFor<typeof updateTask>(
        buildTask({ ...body, id: String(params.id) }),
      ),
    )
  }),
  http.post('/api/v1/tasks/:id/transition', async ({ params, request }) => {
    const body = (await request.json()) as {
      target_status?: Task['status']
    }
    return HttpResponse.json(
      successFor<typeof transitionTask>(
        buildTask({
          id: String(params.id),
          status: body.target_status ?? 'in_progress',
        }),
      ),
    )
  }),
  http.post('/api/v1/tasks/:id/cancel', async ({ params, request }) => {
    const body = (await request.json()) as { reason?: string }
    if (!body.reason) {
      return HttpResponse.json(apiError("Field 'reason' is required"), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof cancelTask>(
        buildTask({ id: String(params.id), status: 'cancelled' }),
      ),
    )
  }),
  http.delete('/api/v1/tasks/:id', () => HttpResponse.json(voidSuccess())),
]
