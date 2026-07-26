import { http, HttpResponse } from 'msw'
import type {
  createEntity,
  deriveOntology,
  EntityResponse,
  getEntity,
  getVersionManifest,
  listDriftReports,
  listEntityVersions,
  syncOrgMemory,
  triggerDriftCheck,
  updateEntity,
} from '@/api/endpoints/ontology'
import type { EntityListMeta, EntityListResponse } from '@/api/types/ontology'
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

/**
 * Build the ``EntityListResponse`` wire envelope for the ontology list
 * endpoint. ``EntityListResponse`` carries catalog-wide ``meta`` aggregates
 * that the generic ``paginatedFor`` cannot model, so this ontology-specific
 * helper keeps the handler in lockstep with the contract instead of inlining
 * the envelope.
 */
function entityListEnvelope(
  data: readonly EntityResponse[] = [],
  opts: {
    meta?: Partial<EntityListMeta>
    nextCursor?: string | null
    limit?: number
  } = {},
): EntityListResponse {
  const nextCursor = opts.nextCursor ?? null
  // Derive the tier counts from the data so the aggregates stay consistent
  // with total_count by default; opts.meta still overrides for bespoke cases.
  const coreCount = data.filter((e) => e.tier === 'core').length
  return {
    data: [...data],
    degraded_sources: [],
    error: null,
    error_detail: null,
    meta: {
      core_count: coreCount,
      user_count: data.length - coreCount,
      total_count: data.length,
      drift_summary: null,
      ...opts.meta,
    },
    pagination: { limit: opts.limit ?? 200, next_cursor: nextCursor, has_more: nextCursor !== null },
    success: true,
  }
}

export const ontologyHandlers = [
  http.get('/api/v1/ontology/entities', () => HttpResponse.json(entityListEnvelope())),
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
  http.post('/api/v1/ontology/admin/derive', () =>
    HttpResponse.json(successFor<typeof deriveOntology>({ derived_count: 0 })),
  ),
  http.post('/api/v1/ontology/admin/sync-org-memory', () =>
    HttpResponse.json(
      successFor<typeof syncOrgMemory>({ status: 'sync_completed', published_count: 0 }),
    ),
  ),
]
