import { http, HttpResponse } from 'msw'
import type {
  createArtifact,
  getArtifact,
  listArtifacts,
} from '@/api/endpoints/artifacts'
import type { Artifact } from '@/api/types/artifacts'
import {
  apiError,
  emptyPage,
  paginatedFor,
  successFor,
  voidSuccess,
} from './helpers'

function buildArtifact(overrides: Partial<Artifact> = {}): Artifact {
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
