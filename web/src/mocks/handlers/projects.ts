import { http, HttpResponse } from 'msw'
import type {
  bulkDeleteProjects,
  createProject,
  getProject,
  getProjectProgress,
  listProjects,
  setProjectAutonomyMode,
} from '@/api/endpoints/projects'
import type {
  Project,
  ProjectAutonomyModeRequest,
  ProjectProgress,
} from '@/api/types/projects'
import {
  apiError,
  emptyPage,
  paginatedFor,
  successFor,
  voidSuccess,
} from './helpers'

function buildProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'project-default',
    name: 'Default Project',
    description: '',
    lead: null,
    lead_name: null,
    plan_id: null,
    deadline: null,
    budget: 0,
    status: 'planning',
    autonomy_mode: null,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

function buildProgress(overrides: Partial<ProjectProgress> = {}): ProjectProgress {
  return {
    project_id: 'project-default',
    project_status: 'planning',
    plan_id: null,
    plan_status: null,
    plan_failure_reason: null,
    objective_title: null,
    items: [],
    counts: { total: 0, done: 0, failed: 0, blocked: 0 },
    critical_path: [],
    contributors: [],
    ...overrides,
  }
}

// ── Default test handlers: empty list, generic single-project lookups. ──
export const projectsHandlers = [
  http.get('/api/v1/projects', () =>
    HttpResponse.json(paginatedFor<typeof listProjects>(emptyPage<Project>())),
  ),
  http.get('/api/v1/projects/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getProject>(buildProject({ id: String(params['id']) })),
    ),
  ),
  http.get('/api/v1/projects/:id/progress', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getProjectProgress>(
        buildProgress({ project_id: String(params['id']) }),
      ),
    ),
  ),
  http.post('/api/v1/projects', async ({ request }) => {
    const body = (await request.json()) as Partial<Project>
    if (!body.name) {
      return HttpResponse.json(apiError("Field 'name' is required"), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof createProject>(
        buildProject({
          id: `project-${body.name}`,
          name: body.name,
          description: body.description ?? '',
          lead: body.lead ?? null,
          deadline: body.deadline ?? null,
          budget: body.budget ?? 0,
        }),
      ),
      { status: 201 },
    )
  }),
  http.patch('/api/v1/projects/:id/autonomy-mode', async ({ params, request }) => {
    const body = (await request.json()) as ProjectAutonomyModeRequest
    return HttpResponse.json(
      successFor<typeof setProjectAutonomyMode>(
        buildProject({
          id: String(params['id']),
          autonomy_mode: body.mode ?? null,
        }),
      ),
    )
  }),
  http.delete('/api/v1/projects/:id', () => HttpResponse.json(voidSuccess())),
  http.post('/api/v1/projects/bulk-delete', async ({ request }) => {
    const body = (await request.json()) as { ids: string[] }
    return HttpResponse.json(
      successFor<typeof bulkDeleteProjects>({ deleted: body.ids, failed: [] }),
    )
  }),
]
