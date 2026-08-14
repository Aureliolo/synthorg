import type { RedTeamReportRecord } from '@/api/types/cockpit'
import type {
  CompletionOracleReportRecord,
  GateVerdictSummary,
} from '@/api/types/gate-verdicts'

import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'

/** Query filters shared by both verdict archives. */
export interface GateVerdictFilters extends PaginationParams {
  readonly execution_id?: string
  readonly task_id?: string
}

/** Peer-review verdict filters: which agent reached them. */
export interface OracleVerdictFilters extends GateVerdictFilters {
  readonly reviewer_agent_id?: string
}

/** Adversarial verdict filters: which agent reached them. */
export interface RedTeamVerdictFilters extends GateVerdictFilters {
  readonly red_team_agent_id?: string
}

/** Fetch a page of archived peer-review verdicts, newest first. */
export async function getCompletionOracleReports(
  params?: OracleVerdictFilters,
): Promise<PaginatedResult<CompletionOracleReportRecord>> {
  const response = await apiClient.get<PaginatedResponse<CompletionOracleReportRecord>>(
    '/completion-oracle/reports',
    { params },
  )
  return unwrapPaginated<CompletionOracleReportRecord>(response)
}

/** Fetch a page of archived adversarial verdicts, newest first. */
export async function getRedTeamReports(
  params?: RedTeamVerdictFilters,
): Promise<PaginatedResult<RedTeamReportRecord>> {
  const response = await apiClient.get<PaginatedResponse<RedTeamReportRecord>>(
    '/red-team/reports',
    { params },
  )
  return unwrapPaginated<RedTeamReportRecord>(response)
}

/** How a reviewer's peer-review verdicts split by kind, over their whole history. */
export async function getCompletionOracleSummary(params?: {
  readonly task_id?: string
  readonly reviewer_agent_id?: string
}): Promise<GateVerdictSummary> {
  const response = await apiClient.get<ApiResponse<GateVerdictSummary>>(
    '/completion-oracle/reports/summary',
    { params },
  )
  return unwrap(response)
}

/** How an adversary's verdicts split by kind, over their whole history. */
export async function getRedTeamSummary(params?: {
  readonly task_id?: string
  readonly red_team_agent_id?: string
}): Promise<GateVerdictSummary> {
  const response = await apiClient.get<ApiResponse<GateVerdictSummary>>(
    '/red-team/reports/summary',
    { params },
  )
  return unwrap(response)
}
