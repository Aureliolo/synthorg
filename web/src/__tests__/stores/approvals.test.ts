import { describe, it, expect, beforeEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useApprovalStore } from '@/stores/approvals'
import type { ApprovalItem, WsEvent } from '@/api/types'

vi.mock('@/api/endpoints/approvals', () => ({
  listApprovals: vi.fn(),
  getApproval: vi.fn(),
  createApproval: vi.fn(),
  approveApproval: vi.fn(),
  rejectApproval: vi.fn(),
}))

const mockApproval: ApprovalItem = {
  id: 'approval-1',
  action_type: 'deploy:production',
  title: 'Deploy to prod',
  description: 'Deploying v2.0',
  requested_by: 'agent-1',
  risk_level: 'high',
  status: 'pending',
  ttl_seconds: 3600,
  task_id: null,
  metadata: {},
  decided_by: null,
  decision_reason: null,
  created_at: '2026-03-12T10:00:00Z',
  decided_at: null,
  expires_at: '2026-03-12T11:00:00Z',
}

describe('useApprovalStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('initializes with empty state', () => {
    const store = useApprovalStore()
    expect(store.approvals).toEqual([])
    expect(store.pendingCount).toBe(0)
  })

  it('computes pendingCount correctly', () => {
    const store = useApprovalStore()
    store.approvals = [
      mockApproval,
      { ...mockApproval, id: 'approval-2', status: 'approved' },
    ]
    expect(store.pendingCount).toBe(1)
  })

  it('handles approval.submitted WS event', () => {
    const store = useApprovalStore()
    const event: WsEvent = {
      event_type: 'approval.submitted',
      channel: 'approvals',
      timestamp: '2026-03-12T10:00:00Z',
      payload: { ...mockApproval },
    }
    store.handleWsEvent(event)
    expect(store.approvals).toHaveLength(1)
  })

  it('handles approval.approved WS event', () => {
    const store = useApprovalStore()
    store.approvals = [mockApproval]
    const event: WsEvent = {
      event_type: 'approval.approved',
      channel: 'approvals',
      timestamp: '2026-03-12T10:01:00Z',
      payload: { id: 'approval-1', status: 'approved', decided_by: 'admin' },
    }
    store.handleWsEvent(event)
    expect(store.approvals[0].status).toBe('approved')
  })
})
