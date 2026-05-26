import { http, HttpResponse } from 'msw'
import type {
  createTrainingPlan,
  executeTrainingPlan,
  getLatestTrainingPlan,
  getTrainingResult,
  previewTrainingPlan,
  TrainingPlanResponse,
  TrainingResultResponse,
  updateTrainingOverrides,
} from '@/api/endpoints/training'
import { successFor } from './helpers'

const NOW = '2026-04-19T00:00:00Z'

function buildPlan(
  overrides: Partial<TrainingPlanResponse> = {},
): TrainingPlanResponse {
  return {
    id: 'plan-default',
    new_agent_id: 'agent-new',
    new_agent_role: 'engineer',
    source_selector_type: 'all',
    enabled_content_types: ['procedural', 'semantic'],
    curation_strategy_type: 'merge',
    volume_caps: [],
    override_sources: [],
    skip_training: false,
    require_review: false,
    status: 'pending',
    created_at: NOW,
    executed_at: null,
    ...overrides,
  }
}

function buildResult(
  overrides: Partial<TrainingResultResponse> = {},
): TrainingResultResponse {
  return {
    id: 'result-default',
    plan_id: 'plan-default',
    new_agent_id: 'agent-new',
    source_agents_used: [],
    items_extracted: [],
    items_after_curation: [],
    items_after_guards: [],
    items_stored: [],
    approval_item_id: null,
    pending_approvals: [],
    review_pending: false,
    errors: [],
    started_at: NOW,
    completed_at: NOW,
    ...overrides,
  }
}

export const trainingHandlers = [
  http.post('/api/v1/agents/:name/training/plan', () =>
    HttpResponse.json(successFor<typeof createTrainingPlan>(buildPlan()), {
      status: 201,
    }),
  ),
  http.post('/api/v1/agents/:name/training/execute', () =>
    HttpResponse.json(successFor<typeof executeTrainingPlan>(buildResult())),
  ),
  http.get('/api/v1/agents/:name/training/result', () =>
    HttpResponse.json(successFor<typeof getTrainingResult>(buildResult())),
  ),
  http.get('/api/v1/agents/:name/training/plan', () =>
    HttpResponse.json(successFor<typeof getLatestTrainingPlan>(buildPlan())),
  ),
  http.post('/api/v1/agents/:name/training/preview', () =>
    HttpResponse.json(successFor<typeof previewTrainingPlan>(buildResult())),
  ),
  http.put('/api/v1/agents/:name/training/plan/:planId/overrides', ({ params }) =>
    HttpResponse.json(
      successFor<typeof updateTrainingOverrides>(
        buildPlan({ id: String(params.planId) }),
      ),
    ),
  ),
]
