import { http, HttpResponse } from 'msw'
import type {
  createEntity,
  EntityResponse,
  getEntity,
  getVersionManifest,
  listDriftReports,
  listEntities,
  listEntityVersions,
  triggerDriftCheck,
  updateEntity,
} from '@/api/endpoints/ontology'
import { emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'

const NOW = '2026-04-19T00:00:00Z'

function buildEntity(
  overrides: Partial<EntityResponse> = {},
): EntityResponse {
  return {
    name: 'default-entity',
    tier: 'user',
    source: 'api',
    definition: 'Default entity',
    fields: [],
    constraints: [],
    disambiguation: '',
    relationships: [],
    created_by: 'user-1',
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

export const ontologyHandlers = [
  http.get('/api/v1/ontology/entities', () =>
    HttpResponse.json(paginatedFor<typeof listEntities>(emptyPage<EntityResponse>())),
  ),
  http.get('/api/v1/ontology/entities/:name', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getEntity>(buildEntity({ name: String(params['name']) })),
    ),
  ),
  http.post('/api/v1/ontology/entities', async ({ request }) => {
    const body = (await request.json()) as { name: string }
    return HttpResponse.json(
      successFor<typeof createEntity>(buildEntity({ name: body.name })),
      { status: 201 },
    )
  }),
  http.put('/api/v1/ontology/entities/:name', async ({ params, request }) => {
    const body = (await request.json()) as Partial<EntityResponse>
    return HttpResponse.json(
      successFor<typeof updateEntity>(
        buildEntity({ ...body, name: String(params['name']) }),
      ),
    )
  }),
  http.delete('/api/v1/ontology/entities/:name', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.get('/api/v1/ontology/entities/:name/versions', () =>
    HttpResponse.json(paginatedFor<typeof listEntityVersions>(emptyPage())),
  ),
  http.get('/api/v1/ontology/manifest', () =>
    HttpResponse.json(successFor<typeof getVersionManifest>({})),
  ),
  http.get('/api/v1/ontology/drift', () =>
    HttpResponse.json(paginatedFor<typeof listDriftReports>(emptyPage())),
  ),
  http.post('/api/v1/ontology/drift/check', () =>
    HttpResponse.json(
      successFor<typeof triggerDriftCheck>({ status: 'drift_check_completed' }),
    ),
  ),
]
