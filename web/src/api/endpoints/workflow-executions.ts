/**
 * Workflow execution endpoints.
 *
 * Mirrors the backend ``synthorg.api.controllers.workflow_executions``
 * surface: list executions for a workflow, cancel an in-flight
 * execution. Field names and the response wire shape match the
 * backend Pydantic ``WorkflowExecution`` model 1:1 (no envelope
 * wrapper around the list -- the controller returns the raw list
 * inside ``ApiResponse.data``).
 */
import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'

/** Mirrors ``synthorg.core.enums.WorkflowExecutionStatus``. */
export type WorkflowExecutionStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

/**
 * Mirrors ``synthorg.engine.workflow.execution_models.WorkflowExecution``.
 * Field names match the backend exactly (``definition_id`` not
 * ``workflow_id``, ``activated_by`` not ``triggered_by``, ``error`` not
 * ``error_message``); the dashboard does not rename them so a future
 * backend field addition lands without a frontend rename pass.
 */
export interface WorkflowExecution {
  /** Unique execution id. */
  id: string
  /** Source ``WorkflowDefinition`` id (NOT the URL path's ``workflow_id``). */
  definition_id: string
  /** Definition revision the execution was activated against. */
  definition_revision: number
  /** Overall execution lifecycle status. */
  status: WorkflowExecutionStatus
  /** Identity of the user / agent that triggered activation. */
  activated_by: string
  /** Project the execution belongs to. */
  project: string
  /** ISO 8601 timestamp the execution record was created (== "started"). */
  created_at: string
  /** ISO 8601 timestamp of the most recent state update. */
  updated_at: string
  /**
   * ISO 8601 completion timestamp; ``null`` until the execution reaches
   * a terminal state (``completed`` / ``failed`` / ``cancelled``).
   */
  completed_at: string | null
  /** Error message when ``status === 'failed'``; ``null`` otherwise. */
  error: string | null
}

/**
 * GET /workflow-executions/by-definition/{workflow_id}
 *
 * Backend returns ``ApiResponse[list[WorkflowExecution]]`` -- a flat
 * list inside ``data``, NOT a ``{ executions: ... }`` wrapper. The
 * unwrap is direct.
 */
export async function listWorkflowExecutions(
  workflowId: string,
): Promise<readonly WorkflowExecution[]> {
  const response = await apiClient.get<ApiResponse<WorkflowExecution[]>>(
    `/workflow-executions/by-definition/${encodeURIComponent(workflowId)}`,
  )
  return unwrap(response)
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
