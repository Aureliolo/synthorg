import { http, HttpResponse } from 'msw'
import type {
  applyPromotion,
  evaluatePromotion,
  getPromotionHistory,
  runPromotionCycle,
} from '@/api/endpoints/promotion'
import type { PromotionEvaluationDTO, PromotionRecordDTO } from '@/api/types'
import { successFor } from './helpers'

const BASE = '/api/v1/promotion'

export function buildPromotionRecord(
  overrides: Partial<PromotionRecordDTO> = {},
): PromotionRecordDTO {
  return {
    id: 'promo-1',
    agent_id: 'agent-1',
    agent_name: 'Dana',
    approval_id: null,
    approved_by: 'auto',
    direction: 'promotion',
    effective_at: '2026-06-15T09:00:00+00:00',
    initiated_by: 'auto',
    model_changed: true,
    new_level: 'senior',
    new_model_id: 'example-large-002',
    old_level: 'mid',
    old_model_id: 'example-large-001',
    ...overrides,
  }
}

export function buildPromotionEvaluation(
  overrides: Partial<PromotionEvaluationDTO> = {},
): PromotionEvaluationDTO {
  return {
    agent_id: 'agent-1',
    criteria_met_count: 2,
    criteria_results: [
      { name: 'tasks_completed', current_value: 12, threshold: 10, met: true, weight: null },
      { name: 'avg_quality_score', current_value: 8.4, threshold: 8, met: true, weight: null },
    ],
    current_level: 'mid',
    direction: 'promotion',
    eligible: true,
    evaluated_at: '2026-06-15T09:00:00+00:00',
    required_criteria_met: true,
    strategy_name: 'default',
    target_level: 'senior',
    ...overrides,
  }
}

export const promotionHandlers = [
  http.get(`${BASE}/:agentId/evaluate`, ({ params }) =>
    HttpResponse.json(
      successFor<typeof evaluatePromotion>(
        buildPromotionEvaluation({ agent_id: String(params['agentId']) }),
      ),
    ),
  ),
  http.get(`${BASE}/:agentId/history`, ({ params }) =>
    HttpResponse.json(
      successFor<typeof getPromotionHistory>([
        buildPromotionRecord({ agent_id: String(params['agentId']) }),
      ]),
    ),
  ),
  http.post(`${BASE}/:agentId/apply`, ({ params }) =>
    HttpResponse.json(
      successFor<typeof applyPromotion>({
        applied: buildPromotionRecord({ agent_id: String(params['agentId']) }),
        request: {
          id: 'req-1',
          agent_id: String(params['agentId']),
          agent_name: 'Dana',
          approval_id: null,
          created_at: '2026-06-15T09:00:00+00:00',
          current_level: 'mid',
          direction: 'promotion',
          status: 'approved',
          target_level: 'senior',
        },
      }),
    ),
  ),
  http.post(`${BASE}/cycle`, () =>
    HttpResponse.json(
      successFor<typeof runPromotionCycle>([buildPromotionRecord()]),
    ),
  ),
]
