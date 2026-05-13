import { http, HttpResponse } from 'msw'
import type {
  listWebhookActivity,
  retryWebhookReceipt,
  WebhookReceipt,
} from '@/api/endpoints/webhooks'
import { successFor } from './helpers'

/**
 * Build a happy-path ``WebhookReceipt`` row for stories and tests.
 * Mirrors the backend Pydantic model field-for-field.
 */
export function buildWebhookReceipt(
  overrides: Partial<WebhookReceipt> = {},
): WebhookReceipt {
  return {
    id: 'whr-000000000001',
    connection_name: 'default-connection',
    event_type: 'workflow.executed',
    status: 'completed',
    received_at: '2026-04-30T10:00:00Z',
    processed_at: '2026-04-30T10:00:01Z',
    payload_json: '{}',
    error: null,
    ...overrides,
  }
}

const defaultReceipts: WebhookReceipt[] = [
  buildWebhookReceipt({
    id: 'whr-000000000001',
    status: 'completed',
  }),
  buildWebhookReceipt({
    id: 'whr-000000000002',
    status: 'failed',
    processed_at: null,
    error: 'connection refused',
  }),
]

export const webhooksHandlers = [
  http.get('/api/v1/webhooks/:connectionName/activity', () =>
    HttpResponse.json(successFor<typeof listWebhookActivity>(defaultReceipts)),
  ),
  http.post('/api/v1/webhooks/receipts/:receiptId/retry', ({ params }) =>
    HttpResponse.json(
      successFor<typeof retryWebhookReceipt>({
        status: 'accepted',
        event_type: 'workflow.executed',
        receipt_id: String(params.receiptId),
      }),
    ),
  ),
]
