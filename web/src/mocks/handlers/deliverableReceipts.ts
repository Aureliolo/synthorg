import { http, HttpResponse } from 'msw'
import type {
  getDeliverableReceipt,
  validateDeliverableReceipt,
} from '@/api/endpoints/deliverableReceipts'
import type {
  DeliverableReceipt,
  ReceiptValidationResult,
} from '@/api/types'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { successFor } from './helpers'

export function buildDeliverableReceipt(
  overrides: Partial<DeliverableReceipt> = {},
): DeliverableReceipt {
  return {
    receipt_id: 'receipt-default',
    task_id: 'task-default',
    project_id: 'project-default',
    execution_id: 'exec-default',
    deliverable_doc_slug: 'doc-default',
    issued_at: '2026-05-20T00:00:00Z',
    total_cost: 0,
    currency: DEFAULT_CURRENCY,
    sources: [],
    decisions: [],
    tests: [],
    red_team: null,
    cassette: null,
    ...overrides,
  }
}

export function buildReceiptValidationResult(
  overrides: Partial<ReceiptValidationResult> = {},
): ReceiptValidationResult {
  return {
    valid: true,
    errors: [],
    ...overrides,
  }
}

// Default test handlers: happy-path receipt + valid result.
export const deliverableReceiptsHandlers = [
  http.get(
    '/api/v1/projects/:projectId/docs/:slug/receipt/validate',
    () =>
      HttpResponse.json(
        successFor<typeof validateDeliverableReceipt>(
          buildReceiptValidationResult(),
        ),
      ),
  ),
  http.get('/api/v1/projects/:projectId/docs/:slug/receipt', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getDeliverableReceipt>(
        buildDeliverableReceipt({
          deliverable_doc_slug: String(params['slug']),
          project_id: String(params['projectId']),
        }),
      ),
    ),
  ),
]
