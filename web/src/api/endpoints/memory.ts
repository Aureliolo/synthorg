/**
 * Memory administration endpoints (CEO / SYSTEM only).
 *
 * Per-agent memory-entry deletion. The backend gates this on the CEO /
 * SYSTEM role; callers should only surface it for those roles.
 */
import { apiClient, unwrapVoid } from '../client'
import type { ApiResponse } from '../types/http'

/** Delete a single memory entry owned by an agent. */
export async function deleteMemoryEntry(
  agentId: string,
  memoryId: string,
): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/admin/memory/agents/${encodeURIComponent(agentId)}/memories/${encodeURIComponent(memoryId)}`,
  )
  unwrapVoid(response)
}
