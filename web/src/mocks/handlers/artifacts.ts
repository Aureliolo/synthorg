import { http, HttpResponse } from 'msw'
import type {
  createArtifact,
  getArtifact,
  listArtifacts,
} from '@/api/endpoints/artifacts'
import type { Artifact } from '@/api/types/artifacts'
import type { PaginatedResponse, PaginationMeta } from '@/api/types/http'
import {
  apiError,
  apiSuccess,
  emptyPage,
  paginatedFor,
  successFor,
  voidSuccess,
} from './helpers'

export function buildArtifact(overrides: Partial<Artifact> = {}): Artifact {
  return {
    id: 'artifact-default',
    type: 'code',
    path: 'src/default.ts',
    task_id: 'task-default',
    created_by: 'agent-default',
    description: 'Default artifact stub',
    project_id: null,
    content_type: 'text/plain',
    size_bytes: 0,
    created_at: '2026-04-19T00:00:00Z',
    ...overrides,
  }
}

// ── Storybook-facing named export: populated paginated list. ──
const mockArtifacts: Artifact[] = [
  {
    id: 'artifact-abc123',
    type: 'code',
    path: 'src/engine/coordinator.py',
    task_id: 'task-001',
    created_by: 'agent-eng-001',
    description: 'Coordinator implementation',
    project_id: 'proj-001',
    content_type: 'text/plain',
    size_bytes: 4096,
    created_at: '2026-03-30T10:00:00Z',
  },
  {
    id: 'artifact-def456',
    type: 'tests',
    path: 'tests/test_coordinator.py',
    task_id: 'task-001',
    created_by: 'agent-qa-001',
    description: 'Coordinator tests',
    project_id: 'proj-001',
    content_type: 'text/plain',
    size_bytes: 2048,
    created_at: '2026-03-30T11:00:00Z',
  },
  {
    id: 'artifact-ghi789',
    type: 'documentation',
    path: 'docs/coordinator.md',
    task_id: 'task-002',
    created_by: 'agent-eng-001',
    description: 'Coordinator documentation',
    project_id: null,
    content_type: 'text/markdown',
    size_bytes: 1024,
    created_at: '2026-03-30T12:00:00Z',
  },
]

const pagination: PaginationMeta = {
  limit: 50,
  next_cursor: null,
  has_more: false,
}

export const artifactsList = [
  http.get('/api/v1/artifacts', () => {
    const body: PaginatedResponse<Artifact> = {
      data: mockArtifacts,
      error: null,
      error_detail: null,
      success: true,
      pagination,
    }
    return HttpResponse.json(body)
  }),
  http.get('/api/v1/artifacts/:id', ({ params }) => {
    const artifact = mockArtifacts.find((a) => a.id === params.id)
    if (!artifact) {
      return HttpResponse.json(apiError('Artifact not found'), { status: 404 })
    }
    return HttpResponse.json(apiSuccess(artifact))
  }),
]

// ── Default test handlers: empty list and generic single-item lookups. ──
export const artifactsHandlers = [
  http.get('/api/v1/artifacts', () =>
    HttpResponse.json(paginatedFor<typeof listArtifacts>(emptyPage<Artifact>())),
  ),
  http.get('/api/v1/artifacts/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getArtifact>(buildArtifact({ id: String(params.id) })),
    ),
  ),
  http.post('/api/v1/artifacts', async ({ request }) => {
    const body = (await request.json()) as {
      type?: Artifact['type']
      path?: string
      task_id?: string
      created_by?: string
      description?: string
      content_type?: string
      project_id?: string | null
    }
    if (!body.type || !body.path || !body.task_id || !body.created_by) {
      return HttpResponse.json(apiError('Missing required fields'), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof createArtifact>(
        buildArtifact({
          id: `artifact-${body.task_id}`,
          type: body.type,
          path: body.path,
          task_id: body.task_id,
          created_by: body.created_by,
          description: body.description ?? '',
          content_type: body.content_type ?? 'text/plain',
          project_id: body.project_id ?? null,
        }),
      ),
      { status: 201 },
    )
  }),
  http.delete('/api/v1/artifacts/:id', () => HttpResponse.json(voidSuccess())),
  http.get('/api/v1/artifacts/:id/content', ({ request }) => {
    const accept = request.headers.get('accept') ?? ''
    if (accept.includes('text')) {
      return new HttpResponse('default artifact content', {
        headers: { 'Content-Type': 'text/plain' },
      })
    }
    return new HttpResponse(new Blob(['default artifact content']), {
      headers: { 'Content-Type': 'application/octet-stream' },
    })
  }),
]
