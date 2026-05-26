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
    const executionId = String(params.executionId ?? 'wfx-cancelled')
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
