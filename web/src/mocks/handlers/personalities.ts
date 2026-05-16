import { http, HttpResponse } from 'msw'
import type {
  createAdminPreset,
  getAdminPreset,
  listAdminPresets,
} from '@/api/endpoints/personalities'
import type {
  PresetDetailResponse,
  PresetSummaryResponse,
} from '@/api/types/dtos.gen'
import { apiSuccess, paginatedEnvelopeFor, successFor } from './helpers'

function summary(overrides: Partial<PresetSummaryResponse> = {}): PresetSummaryResponse {
  return {
    name: 'builtin-default',
    description: 'A built-in personality preset shipped with the runtime.',
    source: 'builtin',
    traits: ['curious'],
    ...overrides,
  }
}

function detail(overrides: Partial<PresetDetailResponse> = {}): PresetDetailResponse {
  return {
    name: overrides.name ?? 'builtin-default',
    source: overrides.source ?? 'builtin',
    description: overrides.description ?? 'A personality preset.',
    created_at: overrides.created_at ?? '2026-01-01T00:00:00Z',
    updated_at: overrides.updated_at ?? '2026-01-01T00:00:00Z',
    ...overrides,
  } as PresetDetailResponse
}

export const personalitiesHandlers = [
  http.get('/api/v1/personalities/presets', () =>
    HttpResponse.json(
      paginatedEnvelopeFor<typeof listAdminPresets>([summary()]),
    ),
  ),
  http.get('/api/v1/personalities/presets/:name', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAdminPreset>(detail({ name: String(params.name) })),
    ),
  ),
  http.post('/api/v1/personalities/presets', async ({ request }) => {
    const body = (await request.json()) as { name: string; description?: string }
    return HttpResponse.json(
      successFor<typeof createAdminPreset>(
        detail({ name: body.name, description: body.description ?? '', source: 'custom' }),
      ),
      { status: 201 },
    )
  }),
  http.put('/api/v1/personalities/presets/:name', ({ params }) =>
    HttpResponse.json(
      apiSuccess(detail({ name: String(params.name), source: 'custom' })),
    ),
  ),
  http.delete('/api/v1/personalities/presets/:name', () =>
    HttpResponse.json(apiSuccess(null)),
  ),
  http.get('/api/v1/personalities/schema', () =>
    HttpResponse.json(apiSuccess({})),
  ),
]
