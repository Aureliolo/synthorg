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

export function buildProject(overrides: Partial<Project> = {}): Project {
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

// ── Storybook-facing named export (preserve existing stories). ──
const mockProjects: Project[] = [
  buildProject({
    id: 'proj-abc123',
    name: 'Engine Rewrite',
    description: 'Rewrite the coordination engine for v2',
    team: ['agent-eng-001', 'agent-eng-002', 'agent-qa-001'],
    lead: 'agent-eng-001',
    task_ids: ['task-001', 'task-002', 'task-003'],
    deadline: '2026-06-01T00:00:00Z',
    budget: 1500,
    status: 'active',
  }),
  buildProject({
    id: 'proj-def456',
    name: 'Documentation Sprint',
    description: 'Update all user-facing documentation',
    team: ['agent-eng-003'],
    lead: 'agent-eng-003',
    task_ids: ['task-004'],
    deadline: null,
    budget: 200,
    status: 'planning',
  }),
]
export const projectsList = [
  http.get('/api/v1/projects', () =>
    HttpResponse.json(
      paginatedFor<typeof listProjects>({
        data: mockProjects,
        limit: 50,
        nextCursor: null,
        hasMore: false,
        pagination: {
          limit: 50,
          next_cursor: null,
          has_more: false,
        },
      }),
    ),
  ),
  http.get('/api/v1/projects/:id', ({ params }) => {
    const project = mockProjects.find((p) => p.id === params.id)
    if (!project) {
      return HttpResponse.json(apiError('Project not found'), { status: 404 })
    }
    return HttpResponse.json(successFor<typeof getProject>(project))
  }),
  http.delete('/api/v1/projects/:id', () => HttpResponse.json(voidSuccess())),
]

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
          team: (body.team ?? []) as string[],
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
