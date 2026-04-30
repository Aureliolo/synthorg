/**
 * Approval-request mock-data builders.
 */

export interface MockApprovalRequest {
  id: string
  title: string
  task_id: string
  agent_id: string
  status: 'pending' | 'approved' | 'rejected'
  risk_tier: 'low' | 'medium' | 'high' | 'critical'
  description: string
  created_at: string
  reviewer_id: string | null
}

export function makeApprovalRequest(
  overrides: Partial<MockApprovalRequest> = {},
): MockApprovalRequest {
  return {
    id: 'approval-001',
    title: 'Deploy to production',
    task_id: 'task-001',
    agent_id: 'agent-001',
    status: 'pending',
    risk_tier: 'medium',
    description: 'Approve the production deploy.',
    created_at: '2026-04-01T12:00:00Z',
    reviewer_id: null,
    ...overrides,
  }
}
