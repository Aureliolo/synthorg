import { http, HttpResponse } from 'msw'
import type {
  createCustomRule,
  CustomRule,
  getCustomRule,
  MetricDescriptor,
  previewRule,
  RuleListItem,
  toggleCustomRule,
  updateCustomRule,
} from '@/api/endpoints/custom-rules'
import type { PaginatedResponse } from '@/api/types/http'
import { apiError, emptyPageEnvelope, successFor, voidSuccess } from './helpers'

const NOW = '2026-04-19T00:00:00Z'

export function buildCustomRule(overrides: Partial<CustomRule> = {}): CustomRule {
  return {
    id: 'rule-default',
    name: 'default-rule',
    description: 'Default custom rule',
    metric_path: 'avg_quality_score',
    comparator: 'lt',
    threshold: 0.5,
    severity: 'warning',
    target_altitudes: ['config_tuning'],
    enabled: true,
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  }
}

export const customRulesHandlers = [
  http.get('/api/v1/meta/custom-rules', () =>
    // Backend returns ``PaginatedResponse[CustomRule]``; the endpoint walks
    // pages via ``paginateAll`` and returns a flat array, so the wire envelope
    // must stay paginated even when empty.
    HttpResponse.json(emptyPageEnvelope<CustomRule>()),
  ),
  http.get('/api/v1/meta/custom-rules/metrics', () =>
    // Backend returns ``PaginatedResponse[MetricDescriptor]``; the
    // endpoint helper flattens it to ``MetricDescriptor[]`` for
    // callers, so the wire envelope must include the pagination
    // metadata even when the page is empty.
    HttpResponse.json({
      data: [],
      error: null,
      error_detail: null,
      pagination: {
        limit: 200,
        next_cursor: null,
        has_more: false,
      },
      success: true,
      degraded_sources: [],
    } satisfies PaginatedResponse<MetricDescriptor>),
  ),
  http.post('/api/v1/meta/custom-rules/preview', async ({ request }) => {
    await request.json()
    return HttpResponse.json(
      successFor<typeof previewRule>({ would_fire: false, match: null }),
    )
  }),
  http.get('/api/v1/meta/custom-rules/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getCustomRule>(buildCustomRule({ id: String(params['id']) })),
    ),
  ),
  http.post('/api/v1/meta/custom-rules', async ({ request }) => {
    const body = (await request.json()) as Partial<CustomRule>
    if (!body.name) {
      return HttpResponse.json(apiError("Field 'name' is required"), { status: 400 })
    }
    return HttpResponse.json(
      successFor<typeof createCustomRule>(buildCustomRule(body)),
      { status: 201 },
    )
  }),
  http.patch('/api/v1/meta/custom-rules/:id', async ({ params, request }) => {
    const body = (await request.json()) as Partial<CustomRule>
    return HttpResponse.json(
      successFor<typeof updateCustomRule>(
        buildCustomRule({ ...body, id: String(params['id']) }),
      ),
    )
  }),
  http.delete('/api/v1/meta/custom-rules/:id', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.post('/api/v1/meta/custom-rules/:id/toggle', ({ params }) =>
    HttpResponse.json(
      successFor<typeof toggleCustomRule>(buildCustomRule({ id: String(params['id']) })),
    ),
  ),
  http.get('/api/v1/meta/rules', () =>
    HttpResponse.json(emptyPageEnvelope<RuleListItem>()),
  ),
]
