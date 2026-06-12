import { http, HttpResponse } from 'msw'
import type {
  createAdminPreset,
  getAdminPreset,
  getPersonalitiesSchema,
  listAdminPresets,
  updateAdminPreset,
} from '@/api/endpoints/personalities'
import type {
  PresetDetailResponse,
  PresetSummaryResponse,
} from '@/api/types/dtos.gen'
import { paginatedFor, successFor, voidSuccess } from './helpers'

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
  // ``satisfies`` (not ``as``) so missing / renamed fields on
  // PresetDetailResponse surface as TypeScript errors at build time
  // rather than letting the mock drift silently out of lockstep with
  // the contract. Every required field on the DTO is spelled out
  // explicitly below; ``overrides`` then narrows / replaces.
  return {
    name: 'builtin-default',
    source: 'builtin',
    description: 'A personality preset.',
    traits: ['curious'],
    agreeableness: 0.5,
    conscientiousness: 0.5,
    extraversion: 0.5,
    openness: 0.5,
    stress_response: 0.5,
    communication_style: 'neutral',
    verbosity: 'balanced',
    collaboration: 'team',
    creativity: 'medium',
    decision_making: 'analytical',
    conflict_approach: 'collaborate',
    risk_tolerance: 'medium',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  } satisfies PresetDetailResponse
}

export const personalitiesHandlers = [
  http.get('/api/v1/personalities/presets', () =>
    HttpResponse.json(
      paginatedFor<typeof listAdminPresets>({
        data: [summary()],
        limit: 200,
        nextCursor: null,
        hasMore: false,
        pagination: { limit: 200, next_cursor: null, has_more: false },
      }),
    ),
  ),
  http.get('/api/v1/personalities/presets/:name', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAdminPreset>(detail({ name: String(params['name']) })),
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
      successFor<typeof updateAdminPreset>(
        detail({ name: String(params['name']), source: 'custom' }),
      ),
    ),
  ),
  http.delete('/api/v1/personalities/presets/:name', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.get('/api/v1/personalities/schema', () =>
    HttpResponse.json(successFor<typeof getPersonalitiesSchema>({})),
  ),
]
