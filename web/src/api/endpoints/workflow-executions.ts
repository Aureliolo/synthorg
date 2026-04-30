/**
 * Workflow execution endpoints.
 *
 * Mirrors the backend ``synthorg.api.controllers.workflow_executions``
 * surface: list executions for a workflow, fetch one execution, cancel
 * an in-flight execution. The list endpoint returns the most recent
 * runs in descending order; ``cancel`` is idempotent and 200s on a
 * already-finished execution.
 */
import { apiClient, unwrap, unwrapVoid } from '../client'
import type { ApiResponse } from '../types/http'

export interface WorkflowExecution {
  id: string
  workflow_id: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  started_at: string | null
  completed_at: string | null
  error_message: string | null
  triggered_by: string | null
}

export interface WorkflowExecutionList {
  executions: readonly WorkflowExecution[]
}

export async function listWorkflowExecutions(
  workflowId: string,
): Promise<readonly WorkflowExecution[]> {
  const response = await apiClient.get<ApiResponse<WorkflowExecutionList>>(
    `/workflow-executions/by-definition/${encodeURIComponent(workflowId)}`,
  )
  return unwrap(response).executions
}

export async function cancelWorkflowExecution(executionId: string): Promise<void> {
  const response = await apiClient.post<ApiResponse<null>>(
    `/workflow-executions/${encodeURIComponent(executionId)}/cancel`,
    {},
  )
  unwrapVoid(response)
}
