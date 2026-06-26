/**
 * Workflow execution endpoints.
 *
 * Mirrors the backend ``synthorg.api.controllers.workflow_executions``
 * surface: list executions for a workflow (cursor-paginated), cancel
 * an in-flight execution. Field names match the backend Pydantic
 * ``WorkflowExecution`` model 1:1. The list endpoint returns
 * ``PaginatedResponse<WorkflowExecution>`` -- an envelope that wraps
 * the rows in ``data`` alongside ``next_cursor`` and ``has_more``
 * pagination metadata.
 */
import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { WorkflowExecution, WorkflowExecutionStatus } from '../types'
import type { ApiResponse, PaginatedResponse } from '../types/http'

export type { WorkflowExecution, WorkflowExecutionStatus }

/**
 * GET /workflow-executions/by-definition/{workflow_id}
 *
 * Backend returns ``PaginatedResponse[WorkflowExecution]`` (cursor
 * pagination). Callers receive a ``PaginatedResult`` carrying the
 * page rows plus ``nextCursor`` / ``hasMore`` so they can drive
 * subsequent fetches.
 */
export async function listWorkflowExecutions(
  workflowId: string,
  params: { cursor?: string | null; limit?: number } = {},
): Promise<PaginatedResult<WorkflowExecution>> {
  const response = await apiClient.get<PaginatedResponse<WorkflowExecution>>(
    `/workflow-executions/by-definition/${encodeURIComponent(workflowId)}`,
    { params },
  )
  return unwrapPaginated<WorkflowExecution>(response)
}

/**
 * POST /workflow-executions/{execution_id}/cancel
 *
 * Backend returns ``ApiResponse[WorkflowExecution]`` (the cancelled
 * execution). The dashboard doesn't currently surface the cancelled
 * execution body, so the function returns ``void`` and discards
 * ``response.data`` after the success check. Switch to ``unwrap`` and
 * propagate the returned execution if a future caller needs it.
 */
export async function cancelWorkflowExecution(executionId: string): Promise<void> {
  const response = await apiClient.post<ApiResponse<WorkflowExecution>>(
    `/workflow-executions/${encodeURIComponent(executionId)}/cancel`,
    {},
  )
  // The endpoint returns ApiResponse<WorkflowExecution>, but the UI
  // doesn't render the cancelled execution. unwrap validates the
  // envelope's success flag and returns the typed data; we discard
  // it.
  unwrap(response)
}
