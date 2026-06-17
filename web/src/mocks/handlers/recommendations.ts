import { http, HttpResponse } from 'msw'
import type {
  approveRecommendation,
  getRefreshStatus,
  listModelRecommendations,
  rejectRecommendation,
  triggerRefresh,
} from '@/api/endpoints/recommendations'
import type { UpgradeRecommendationDTO } from '@/api/types'
import { successFor } from './helpers'

const BASE = '/api/v1/providers/model-refresh'

export function buildRecommendation(
  overrides: Partial<UpgradeRecommendationDTO> = {},
): UpgradeRecommendationDTO {
  return {
    id: 'rec-1',
    provider_name: 'example-provider',
    current_model_id: 'example-large-001',
    recommended_model_id: 'example-large-002',
    family: 'example-large',
    current_generation: 1,
    recommended_generation: 2,
    score: 0.82,
    reason: 'Newer in-family generation with matching capabilities.',
    agent_ids: ['agent-1', 'agent-2'],
    status: 'pending',
    created_at: '2026-06-15T09:00:00+00:00',
    decided_at: null,
    decided_by: null,
    ...overrides,
  }
}

export const recommendationsHandlers = [
  http.get(`${BASE}/recommendations`, () =>
    HttpResponse.json(
      successFor<typeof listModelRecommendations>([buildRecommendation()]),
    ),
  ),
  http.post(`${BASE}/recommendations/:id/approve`, ({ params }) =>
    HttpResponse.json(
      successFor<typeof approveRecommendation>(
        buildRecommendation({ id: String(params['id']), status: 'approved' }),
      ),
    ),
  ),
  http.post(`${BASE}/recommendations/:id/reject`, ({ params }) =>
    HttpResponse.json(
      successFor<typeof rejectRecommendation>(
        buildRecommendation({ id: String(params['id']), status: 'rejected' }),
      ),
    ),
  ),
  http.post(`${BASE}/refresh`, () =>
    HttpResponse.json(
      successFor<typeof triggerRefresh>({
        providers_scanned: 1,
        added_count: 0,
        stale_count: 0,
        recommended_count: 1,
        auto_applied_count: 0,
      }),
    ),
  ),
  http.get(`${BASE}/status`, () =>
    HttpResponse.json(
      successFor<typeof getRefreshStatus>({
        mode: 'reconcile_recommend',
        interval_seconds: 86400,
        auto_apply_within_family: false,
      }),
    ),
  ),
]
