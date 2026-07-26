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
import { apiClient, unwrapPaginated, unwrapVoid, type PaginatedResult } from '../client'
import type { WorkflowExecution, WorkflowExecutionStatus } from '@/api/types/workflows'
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
 * The backend returns the cancelled execution, but the dashboard does not
 * render it, so the body is typed as ``null`` and validated for success via
 * ``unwrapVoid``. Type the response as ``ApiResponse<WorkflowExecution>`` and
 * use ``unwrap`` instead if a future caller needs the returned execution.
 */
export async function cancelWorkflowExecution(executionId: string): Promise<void> {
  const response = await apiClient.post<ApiResponse<null>>(
    `/workflow-executions/${encodeURIComponent(executionId)}/cancel`,
    {},
  )
  unwrapVoid(response)
}
