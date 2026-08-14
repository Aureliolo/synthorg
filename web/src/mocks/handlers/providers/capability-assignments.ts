import { http, HttpResponse } from 'msw'
import type {
  applyCapabilityRecommendation,
  getCapabilityClassifierModel,
  listCapabilityAssignments,
  recommendAllCapabilities,
  recommendCapabilityLevel,
  setCapabilityOverride,
} from '@/api/endpoints/providers'
import type {
  ClassifierModelDTO,
  CapabilityAssignmentsResponse,
  CapabilityRecommendationsResponse,
} from '@/api/types/providers'
import { apiSuccess, successFor } from '../helpers'

const BASE = '/api/v1/providers/capability-assignments'

function buildAssignments(): CapabilityAssignmentsResponse {
  return {
    assignments: [
      {
        provider: 'local-host',
        model_id: 'tiny-7b',
        capability: 'basic',
        provenance: 'heuristic',
        confidence: 0.7,
        reason: 'parameter_count=7000000000',
        is_override: false,
      },
      {
        provider: 'local-host',
        model_id: 'huge-120b',
        capability: 'expert',
        provenance: 'operator',
        confidence: 1,
        reason: 'operator override',
        is_override: true,
      },
    ],
  }
}

function buildRecommendations(): CapabilityRecommendationsResponse {
  return {
    recommendations: [
      {
        provider: 'local-host',
        model_id: 'tiny-7b',
        capability: 'basic',
        confidence: 0.85,
        rationale: 'small local model',
      },
    ],
  }
}

function buildClassifierModel(): ClassifierModelDTO {
  return { provider: '', model_id: '', enabled: false }
}

export const capabilityAssignmentsHandlers = [
  http.get(BASE, () =>
    HttpResponse.json(successFor<typeof listCapabilityAssignments>(buildAssignments())),
  ),
  http.get(`${BASE}/classifier-model`, () =>
    HttpResponse.json(
      successFor<typeof getCapabilityClassifierModel>(buildClassifierModel()),
    ),
  ),
  http.put(`${BASE}/classifier-model`, async ({ request }) => {
    // Echo the stored ref back, mirroring the real controller so a test that
    // sets the classifier sees the recommend actions enable.
    const body = (await request.json()) as ClassifierModelDTO
    return HttpResponse.json(apiSuccess(body))
  }),
  http.post(`${BASE}/recommend-all`, () =>
    HttpResponse.json(
      successFor<typeof recommendAllCapabilities>(buildRecommendations()),
    ),
  ),
  http.post(`${BASE}/apply`, () =>
    HttpResponse.json(
      successFor<typeof applyCapabilityRecommendation>(buildAssignments()),
    ),
  ),
  http.post(`${BASE}/:provider/:modelId/recommend`, () =>
    HttpResponse.json(
      successFor<typeof recommendCapabilityLevel>(buildRecommendations()),
    ),
  ),
  http.put(`${BASE}/:provider/:modelId`, () =>
    HttpResponse.json(successFor<typeof setCapabilityOverride>(buildAssignments())),
  ),
]
