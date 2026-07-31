/**
 * Memory administration endpoints (CEO / SYSTEM only).
 *
 * Per-agent memory-entry deletion. The backend gates this on the CEO /
 * SYSTEM role; callers should only surface it for those roles.
 */
import { apiClient, unwrap, unwrapVoid } from '../client'
import type { ApiResponse } from '../types/http'
import type { EmbedderProbeResponse } from '../types/system'

/**
 * Measure a candidate embedder's vector width by asking the model.
 *
 * Issues one real embedding call, so it is only ever sent for a binding the
 * operator has actually selected -- never swept across a catalogue, where on
 * a metered provider it would bill per model to populate a dropdown.
 */
export async function probeEmbedder(
  provider: string,
  modelId: string,
  signal?: AbortSignal,
): Promise<EmbedderProbeResponse> {
  const response = await apiClient.post<ApiResponse<EmbedderProbeResponse>>(
    '/admin/memory/embedder/probe',
    { provider, model_id: modelId },
    signal ? { signal } : {},
  )
  return unwrap(response)
}

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
