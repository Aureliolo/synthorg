import {
  apiClient,
  unwrap,
  unwrapPaginated,
  unwrapVoid,
  type PaginatedResult,
} from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type {
  CheckpointRecord,
  FineTuneRequest,
  FineTuneRun,
  FineTuneStage,
  FineTuneStatus,
  PreflightResult,
} from '@/api/types/fine-tuning'

/** Pipeline stages considered "active" (in progress). */
export const ACTIVE_STAGES: ReadonlySet<FineTuneStage> = new Set<FineTuneStage>([
  'generating_data',
  'mining_negatives',
  'training',
  'evaluating',
  'deploying',
])

// -- API functions ---------------------------------------------------

const BASE = '/admin/memory/fine-tune'

export async function startFineTune(
  request: FineTuneRequest,
): Promise<FineTuneStatus> {
  const response = await apiClient.post<ApiResponse<FineTuneStatus>>(BASE, request)
  return unwrap(response)
}

export async function resumeFineTune(runId: string): Promise<FineTuneStatus> {
  const response = await apiClient.post<ApiResponse<FineTuneStatus>>(
    `${BASE}/resume/${runId}`,
  )
  return unwrap(response)
}

export async function getFineTuneStatus(): Promise<FineTuneStatus> {
  const response = await apiClient.get<ApiResponse<FineTuneStatus>>(`${BASE}/status`)
  return unwrap(response)
}

export async function cancelFineTune(): Promise<FineTuneStatus> {
  const response = await apiClient.post<ApiResponse<FineTuneStatus>>(`${BASE}/cancel`)
  return unwrap(response)
}

export async function runPreflight(
  request: FineTuneRequest,
): Promise<PreflightResult> {
  const response = await apiClient.post<ApiResponse<PreflightResult>>(
    `${BASE}/preflight`,
    request,
  )
  return unwrap(response)
}

export async function listCheckpoints(
  cursor: string | null = null,
  limit = 50,
): Promise<PaginatedResult<CheckpointRecord>> {
  const params: Record<string, string | number> = { limit }
  if (cursor !== null) params['cursor'] = cursor
  const response = await apiClient.get<PaginatedResponse<CheckpointRecord>>(
    `${BASE}/checkpoints`,
    { params },
  )
  return unwrapPaginated<CheckpointRecord>(response)
}

export async function deployCheckpoint(checkpointId: string): Promise<CheckpointRecord> {
  const response = await apiClient.post<ApiResponse<CheckpointRecord>>(
    `${BASE}/checkpoints/${checkpointId}/deploy`,
  )
  return unwrap(response)
}

export async function rollbackCheckpoint(
  checkpointId: string,
): Promise<CheckpointRecord> {
  const response = await apiClient.post<ApiResponse<CheckpointRecord>>(
    `${BASE}/checkpoints/${checkpointId}/rollback`,
  )
  return unwrap(response)
}

export async function deleteCheckpoint(checkpointId: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `${BASE}/checkpoints/${checkpointId}`,
  )
  unwrapVoid(response)
}

export async function listRuns(
  cursor: string | null = null,
  limit = 50,
): Promise<PaginatedResult<FineTuneRun>> {
  const params: Record<string, string | number> = { limit }
  if (cursor !== null) params['cursor'] = cursor
  const response = await apiClient.get<PaginatedResponse<FineTuneRun>>(`${BASE}/runs`, {
    params,
  })
  return unwrapPaginated<FineTuneRun>(response)
}
