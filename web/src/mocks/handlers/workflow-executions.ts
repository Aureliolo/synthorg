import { http, HttpResponse } from 'msw'
import type {
  listWorkflowExecutions,
  WorkflowExecution,
} from '@/api/endpoints/workflow-executions'
import { apiSuccess, successFor } from './helpers'

/**
 * Build a happy-path ``WorkflowExecution`` row for stories and tests.
 * Mirrors the backend Pydantic model field-for-field.
 */
export function buildWorkflowExecution(
  overrides: Partial<WorkflowExecution> = {},
): WorkflowExecution {
  return {
    id: 'wfx-000000000001',
    definition_id: 'wf-default',
    definition_revision: 1,
    status: 'completed',
    activated_by: 'test-user',
    project: 'default',
    created_at: '2026-04-30T10:00:00Z',
    updated_at: '2026-04-30T10:05:00Z',
    completed_at: '2026-04-30T10:05:00Z',
    error: null,
    ...overrides,
  }
}

const defaultExecutions: WorkflowExecution[] = [
  buildWorkflowExecution({
    id: 'wfx-000000000001',
    status: 'completed',
  }),
  buildWorkflowExecution({
    id: 'wfx-000000000002',
    status: 'running',
    completed_at: null,
  }),
]

export const workflowExecutionsHandlers = [
  http.get('/api/v1/workflow-executions/by-definition/:workflowId', () =>
    HttpResponse.json(
      successFor<typeof listWorkflowExecutions>(defaultExecutions),
    ),
  ),

  // Backend returns ApiResponse<WorkflowExecution> from cancel; the
  // MSW handler returns a representative cancelled execution so the
  // wire shape matches the typed client.
  http.post('/api/v1/workflow-executions/:executionId/cancel', ({ params }) => {
    const executionId = String(params.executionId ?? 'wfx-cancelled')
    return HttpResponse.json(
      apiSuccess<WorkflowExecution>(
        buildWorkflowExecution({
          id: executionId,
          status: 'cancelled',
          completed_at: '2026-04-30T10:10:00Z',
        }),
      ),
    )
  }),
]
