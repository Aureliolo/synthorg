import {
  apiClient,
  unwrap,
  unwrapPaginated,
  unwrapVoid,
  type PaginatedResult,
} from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'

// -- Types -----------------------------------------------------------

export type FineTuneStage =
  | 'idle'
  | 'generating_data'
  | 'mining_negatives'
  | 'training'
  | 'evaluating'
  | 'deploying'
  | 'complete'
  | 'failed'

export interface FineTuneStatus {
  run_id: string | null
  stage: FineTuneStage
  progress: number | null
  error: string | null
}

export interface EvalMetrics {
  ndcg_at_10: number
  recall_at_10: number
  base_ndcg_at_10: number
  base_recall_at_10: number
  improvement_ndcg: number
  improvement_recall: number
}

export interface CheckpointRecord {
  id: string
  run_id: string
  model_path: string
  base_model: string
  doc_count: number
  eval_metrics: EvalMetrics | null
  size_bytes: number
  created_at: string
  is_active: boolean
  backup_config_json: string | null
}

export interface FineTuneRunConfig {
  source_dir: string
  base_model: string
  output_dir: string
  epochs: number
  learning_rate: number
  temperature: number
  top_k: number
  batch_size: number
  validation_split: number
}

export interface FineTuneRun {
  id: string
  stage: FineTuneStage
  progress: number | null
  error: string | null
  config: FineTuneRunConfig
  started_at: string
  updated_at: string
  completed_at: string | null
  duration_seconds: number | null
  stages_completed: string[]
}

export interface PreflightCheck {
  name: string
  status: 'pass' | 'warn' | 'fail'
  message: string
  detail: string | null
}

export interface PreflightResult {
  checks: PreflightCheck[]
  recommended_batch_size: number | null
  can_proceed: boolean
}

export interface StartFineTuneRequest {
  source_dir: string
  base_model?: string | null
  output_dir?: string | null
  epochs?: number | null
  learning_rate?: number | null
  temperature?: number | null
  top_k?: number | null
  batch_size?: number | null
  validation_split?: number | null
  resume_run_id?: string | null
}

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
  request: StartFineTuneRequest,
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
  request: StartFineTuneRequest,
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
