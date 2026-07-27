/**
 * Webhook activity endpoints.
 */
import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'
import type { WebhookReceipt } from '@/api/types/integrations'

/**
 * GET /webhooks/{connection_name}/activity
 *
 * Backend returns ``PaginatedResponse[WebhookReceipt]`` (opaque cursor
 * paging). Pass ``params.cursor`` to fetch a follow-on page and
 * ``params.limit`` to size it; the returned ``nextCursor`` / ``hasMore``
 * drive the load-more control.
 */
export async function listWebhookActivity(
  connectionName: string,
  params?: PaginationParams,
): Promise<PaginatedResult<WebhookReceipt>> {
  const response = await apiClient.get<PaginatedResponse<WebhookReceipt>>(
    `/webhooks/${encodeURIComponent(connectionName)}/activity`,
    { params },
  )
  return unwrapPaginated<WebhookReceipt>(response)
}

export interface RetryWebhookReceiptResponse {
  status: string
  event_type: string
  receipt_id: string
}

/**
 * POST /webhooks/receipts/{id}/retry
 *
 * Asks the backend to re-publish the stored payload of a failed
 * receipt. The 202 response carries the updated receipt id so the
 * dashboard can correlate bulk-retry success / failure per row.
 */
export async function retryWebhookReceipt(
  receiptId: string,
): Promise<RetryWebhookReceiptResponse> {
  const response = await apiClient.post<ApiResponse<RetryWebhookReceiptResponse>>(
    `/webhooks/receipts/${encodeURIComponent(receiptId)}/retry`,
  )
  return unwrap(response)
}
