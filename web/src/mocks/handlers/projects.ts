import { http, HttpResponse } from 'msw'
import type {
  createProject,
  getProject,
  listProjects,
} from '@/api/endpoints/projects'
import type { Project } from '@/api/types/projects'
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
    team: [],
    lead: null,
    task_ids: [],
    deadline: null,
    budget: 0,
    status: 'planning',
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
      successFor<typeof getProject>(buildProject({ id: String(params.id) })),
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
          team: (body.team ?? []),
          lead: body.lead ?? null,
          deadline: body.deadline ?? null,
          budget: body.budget ?? 0,
        }),
      ),
      { status: 201 },
    )
  }),
  http.delete('/api/v1/projects/:id', () => HttpResponse.json(voidSuccess())),
]
