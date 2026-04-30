/**
 * Webhook activity endpoints.
 *
 * Mirrors the backend ``synthorg.api.controllers.webhooks`` activity
 * surface: list recent inbound webhook deliveries for a connection,
 * including delivery status, payload size, and any error captured by
 * the receiver.
 */
import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'

export interface WebhookActivityEntry {
  id: string
  connection_name: string
  event_type: string
  received_at: string
  status: 'delivered' | 'failed' | 'rejected'
  status_code: number | null
  payload_bytes: number | null
  error: string | null
}

export interface WebhookActivityList {
  entries: readonly WebhookActivityEntry[]
}

export async function listWebhookActivity(
  connectionName: string,
): Promise<readonly WebhookActivityEntry[]> {
  const response = await apiClient.get<ApiResponse<WebhookActivityList>>(
    `/webhooks/${encodeURIComponent(connectionName)}/activity`,
  )
  return unwrap(response).entries
}
