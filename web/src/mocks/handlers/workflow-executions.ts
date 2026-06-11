import { http, HttpResponse } from 'msw'
import type {
  listWorkflowExecutions,
  WorkflowExecution,
} from '@/api/endpoints/workflow-executions'
import { apiSuccess, paginatedFor } from './helpers'

/**
 * Build a happy-path ``WorkflowExecution`` row for stories and tests.
 * Mirrors the backend Pydantic model field-for-field.
 */
function buildWorkflowExecution(
  overrides: Partial<WorkflowExecution> = {},
): WorkflowExecution {
  return {
    id: '3fa85f64-5717-4562-b3fc-2c963f66afa6',
    definition_id: '123e4567-e89b-12d3-a456-426614174000',
    definition_revision: 1,
    version: 1,
    status: 'completed',
    activated_by: 'test-user',
    project: 'default',
    created_at: '2026-04-30T10:00:00Z',
    updated_at: '2026-04-30T10:05:00Z',
    completed_at: '2026-04-30T10:05:00Z',
    error: null,
    node_executions: [],
    ...overrides,
  }
}

const defaultExecutions: WorkflowExecution[] = [
  buildWorkflowExecution({
    id: '3fa85f64-5717-4562-b3fc-2c963f66afa6',
    status: 'completed',
  }),
  buildWorkflowExecution({
    id: '7c9e6679-7425-40de-944b-e07fc1f90ae7',
    status: 'running',
    completed_at: null,
  }),
]

export const workflowExecutionsHandlers = [
  http.get('/api/v1/workflow-executions/by-definition/:workflowId', ({ params }) => {
    // Reflect the requested workflow id so consumers (tests + Storybook)
    // see ``definition_id`` matching the URL they fetched, not the
    // hardcoded fallback that ``buildWorkflowExecution`` defaults to.
    // Otherwise a test that asserts on ``definition_id`` always sees
    // ``wf-default`` no matter which workflow it just queried.
    const workflowId = String(params.workflowId ?? 'wf-default')
    const rows = defaultExecutions.map((row) => ({
      ...row,
      definition_id: workflowId,
    }))
    return HttpResponse.json(
      paginatedFor<typeof listWorkflowExecutions>({
        data: rows,
        limit: 50,
        nextCursor: null,
        hasMore: false,
        pagination: { limit: 50, next_cursor: null, has_more: false },
      }),
    )
  }),

  // Backend returns ApiResponse<WorkflowExecution> from cancel; the
  // MSW handler returns a representative cancelled execution so the
  // wire shape matches the typed client.
  http.post('/api/v1/workflow-executions/:executionId/cancel', ({ params }) => {
    const executionId = String(
      params.executionId ?? '9b2e4c6a-1d3f-4a5b-8c7d-0e1f2a3b4c5d',
    )
    // ``updated_at`` and ``completed_at`` carry the same cancellation
    // timestamp because the backend emits them together when a run
    // transitions to the terminal ``cancelled`` state. Letting them
    // diverge here would let consumers latch onto the wrong field for
    // "last activity" sort keys and miss the regression in tests.
    const cancelledAt = '2026-04-30T10:10:00Z'
    return HttpResponse.json(
      apiSuccess<WorkflowExecution>(
        buildWorkflowExecution({
          id: executionId,
          status: 'cancelled',
          updated_at: cancelledAt,
          completed_at: cancelledAt,
        }),
      ),
    )
  }),
]
