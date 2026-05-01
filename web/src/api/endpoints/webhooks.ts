/**
 * Webhook activity endpoints.
 *
 * Mirrors the backend ``synthorg.integrations.connections.models.WebhookReceipt``
 * model. Field names match the backend exactly so a future schema change
 * lands without a rename pass.
 */
import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'

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
  /** Provider-specific event type (free-form string; ``""`` if not provided). */
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
 * Backend returns ``ApiResponse[tuple[WebhookReceipt, ...]]`` -- a flat
 * tuple inside ``data``, NOT a ``{ entries: ... }`` wrapper. JSON
 * serialisation flattens the tuple to a list. The unwrap is direct.
 */
export async function listWebhookActivity(
  connectionName: string,
): Promise<readonly WebhookReceipt[]> {
  const response = await apiClient.get<ApiResponse<WebhookReceipt[]>>(
    `/webhooks/${encodeURIComponent(connectionName)}/activity`,
  )
  return unwrap(response)
}
