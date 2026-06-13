import { http, HttpResponse } from 'msw'
import type {
  approveApproval,
  createApproval,
  getApproval,
  listApprovals,
  rejectApproval,
} from '@/api/endpoints/approvals'
import type { ApprovalRiskLevel } from '@/api/types/enums'
import type { ApprovalResponse } from '@/api/types/approvals'
import { apiError, emptyPage, paginatedFor, successFor } from './helpers'

function buildApproval(
  overrides: Partial<ApprovalResponse> = {},
): ApprovalResponse {
  return {
    id: 'approval-default',
    action_type: 'generic',
    title: 'Approval request',
    description: 'Default approval stub',
    requested_by: 'agent-default',
    risk_level: 'low',
    source: 'review_gate',
    status: 'pending',
    task_id: null,
    metadata: {},
    decided_by: null,
    decision_reason: null,
    created_at: '2026-04-19T00:00:00Z',
    decided_at: null,
    expires_at: null,
    consumed_at: null,
    evidence_package: null,
    seconds_remaining: null,
    urgency_level: 'normal',
    ...overrides,
  }
}

export const approvalsHandlers = [
  http.get('/api/v1/approvals', () =>
    HttpResponse.json(paginatedFor<typeof listApprovals>(emptyPage())),
  ),
  http.get('/api/v1/approvals/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getApproval>(buildApproval({ id: String(params['id']) })),
    ),
  ),
  http.post('/api/v1/approvals', async ({ request }) => {
    const body = (await request.json()) as {
      action_type?: string
      title?: string
      description?: string
      risk_level?: ApprovalRiskLevel
      task_id?: string
    }
    if (!body.action_type || !body.title || !body.risk_level) {
      return HttpResponse.json(apiError('Missing required fields'), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof createApproval>(
        buildApproval({
          id: `approval-${body.action_type}`,
          action_type: body.action_type,
          title: body.title,
          description: body.description ?? '',
          risk_level: body.risk_level,
          task_id: body.task_id ?? null,
        }),
      ),
      { status: 201 },
    )
  }),
  http.post('/api/v1/approvals/:id/approve', ({ params }) =>
    HttpResponse.json(
      successFor<typeof approveApproval>(
        buildApproval({
          id: String(params['id']),
          status: 'approved',
          decided_at: '2026-04-19T00:00:00Z',
          decided_by: 'user-1',
        }),
      ),
    ),
  ),
  http.post('/api/v1/approvals/:id/reject', async ({ params, request }) => {
    const body = (await request.json()) as { reason?: string }
    if (!body.reason) {
      return HttpResponse.json(apiError("Field 'reason' is required"), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof rejectApproval>(
        buildApproval({
          id: String(params['id']),
          status: 'rejected',
          decision_reason: body.reason,
          decided_at: '2026-04-19T00:00:00Z',
          decided_by: 'user-1',
        }),
      ),
    )
  }),
]
