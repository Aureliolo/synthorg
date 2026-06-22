/**
 * Webhook activity endpoints.
 *
 * Mirrors the backend ``synthorg.integrations.connections.models.WebhookReceipt``
 * model. Field names match the backend exactly so a future schema change
 * lands without a rename pass.
 */
import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'

/**
 * Mirrors ``synthorg.integrations.connections.models.WebhookReceipt``.
 * The backend uses an open ``status`` string (default ``"received"``);
 * common values include ``received`` / ``processing`` / ``completed``
 * / ``failed``. The frontend treats it as an open string and renders
 * the raw value in the receipt log.
 */
export interface WebhookReceipt {
  /** Unique receipt identifier. */
  id: string
  /** Source connection name. */
  connection_name: string
  /** Provider-specific event type (non-blank; always set by the backend). */
  event_type: string
  /** Processing status (``"received"`` / ``"processing"`` / ``"completed"`` / ``"failed"``). */
  status: string
  /** ISO 8601 timestamp when the webhook was received. */
  received_at: string
  /** ISO 8601 timestamp when processing finished; ``null`` while in flight. */
  processed_at: string | null
  /** Raw payload as JSON string (``""`` when no body was captured). */
  payload_json: string
  /** Error message if processing failed; ``null`` otherwise. */
  error: string | null
}

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
