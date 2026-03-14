import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useApprovalStore } from '@/stores/approvals'
import type { ApprovalItem, WsEvent } from '@/api/types'

const flushPromises = () => new Promise((r) => setTimeout(r, 0))

const mockListApprovals = vi.fn()
const mockGetApproval = vi.fn()
const mockCreateApproval = vi.fn()
const mockApproveApproval = vi.fn()
const mockRejectApproval = vi.fn()

vi.mock('@/api/endpoints/approvals', () => ({
  listApprovals: (...args: unknown[]) => mockListApprovals(...args),
  getApproval: (...args: unknown[]) => mockGetApproval(...args),
  createApproval: (...args: unknown[]) => mockCreateApproval(...args),
  approveApproval: (...args: unknown[]) => mockApproveApproval(...args),
  rejectApproval: (...args: unknown[]) => mockRejectApproval(...args),
}))

const mockApproval: ApprovalItem = {
  id: 'approval-1',
  action_type: 'deploy:production',
  title: 'Deploy to prod',
  description: 'Deploying v2.0',
  requested_by: 'agent-1',
  risk_level: 'high',
  status: 'pending',
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
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.restoreAllMocks()
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

  describe('fetchApprovals', () => {
    it('fetches and populates approvals', async () => {
      mockListApprovals.mockResolvedValue({ data: [mockApproval], total: 1 })

      const store = useApprovalStore()
      await store.fetchApprovals()

      expect(store.approvals).toEqual([mockApproval])
      expect(store.total).toBe(1)
      expect(store.loading).toBe(false)
    })

    it('sets error on failure', async () => {
      mockListApprovals.mockRejectedValue(new Error('Network error'))

      const store = useApprovalStore()
      await store.fetchApprovals()

      expect(store.error).toBe('Network error')
      expect(store.loading).toBe(false)
    })

    it('passes filters to API', async () => {
      mockListApprovals.mockResolvedValue({ data: [], total: 0 })

      const store = useApprovalStore()
      await store.fetchApprovals({ status: 'pending' })

      expect(mockListApprovals).toHaveBeenCalledWith({ status: 'pending' })
    })
  })

  describe('approve', () => {
    it('updates approval in list on success', async () => {
      const approved = { ...mockApproval, status: 'approved' as const, decided_by: 'admin' }
      mockApproveApproval.mockResolvedValue(approved)

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      const result = await store.approve('approval-1', { comment: 'LGTM' })

      expect(result).toEqual(approved)
      expect(store.approvals[0].status).toBe('approved')
    })

    it('returns null and sets error on failure', async () => {
      mockApproveApproval.mockRejectedValue(new Error('Forbidden'))

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      const result = await store.approve('approval-1')

      expect(result).toBeNull()
      expect(store.error).toBe('Forbidden')
    })

    it('clears error before making request', async () => {
      mockApproveApproval.mockRejectedValue(new Error('fail'))

      const store = useApprovalStore()
      store.approvals = [mockApproval]

      await store.approve('approval-1')
      expect(store.error).toBe('fail')

      mockApproveApproval.mockResolvedValue({ ...mockApproval, status: 'approved' })
      await store.approve('approval-1')
      expect(store.error).toBeNull()
    })
  })

  describe('reject', () => {
    it('updates approval in list on success', async () => {
      const rejected = {
        ...mockApproval,
        status: 'rejected' as const,
        decided_by: 'admin',
        decision_reason: 'Too risky',
      }
      mockRejectApproval.mockResolvedValue(rejected)

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      const result = await store.reject('approval-1', { reason: 'Too risky' })

      expect(result).toEqual(rejected)
      expect(store.approvals[0].status).toBe('rejected')
      expect(store.approvals[0].decision_reason).toBe('Too risky')
    })

    it('returns null and sets error on failure', async () => {
      mockRejectApproval.mockRejectedValue(new Error('Not found'))

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      const result = await store.reject('approval-1', { reason: 'test' })

      expect(result).toBeNull()
      expect(store.error).toBe('Not found')
    })
  })

  describe('WS events', () => {
    it('handles approval.submitted WS event', async () => {
      mockGetApproval.mockResolvedValue(mockApproval)

      const store = useApprovalStore()
      const event: WsEvent = {
        event_type: 'approval.submitted',
        channel: 'approvals',
        timestamp: '2026-03-12T10:00:00Z',
        payload: { approval_id: 'approval-1', status: 'pending', action_type: 'deploy:production', risk_level: 'high' },
      }
      store.handleWsEvent(event)
      await flushPromises()

      expect(mockGetApproval).toHaveBeenCalledWith('approval-1')
      expect(store.approvals).toHaveLength(1)
      expect(store.total).toBe(1)
    })

    it('handles approval.approved WS event', async () => {
      const approved = { ...mockApproval, status: 'approved' as const, decided_by: 'admin' }
      mockGetApproval.mockResolvedValue(approved)

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      const event: WsEvent = {
        event_type: 'approval.approved',
        channel: 'approvals',
        timestamp: '2026-03-12T10:01:00Z',
        payload: { approval_id: 'approval-1', status: 'approved', action_type: 'deploy:production', risk_level: 'high' },
      }
      store.handleWsEvent(event)
      await flushPromises()

      expect(mockGetApproval).toHaveBeenCalledWith('approval-1')
      expect(store.approvals[0].status).toBe('approved')
    })

    it('handles approval.rejected WS event', async () => {
      const rejected = { ...mockApproval, status: 'rejected' as const, decided_by: 'admin', decision_reason: 'Too risky' }
      mockGetApproval.mockResolvedValue(rejected)

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      const event: WsEvent = {
        event_type: 'approval.rejected',
        channel: 'approvals',
        timestamp: '2026-03-12T10:01:00Z',
        payload: { approval_id: 'approval-1', status: 'rejected', action_type: 'deploy:production', risk_level: 'high' },
      }
      store.handleWsEvent(event)
      await flushPromises()

      expect(mockGetApproval).toHaveBeenCalledWith('approval-1')
      expect(store.approvals[0].status).toBe('rejected')
      expect(store.approvals[0].decision_reason).toBe('Too risky')
    })

    it('handles approval.expired WS event', async () => {
      const expired = { ...mockApproval, status: 'expired' as const }
      mockGetApproval.mockResolvedValue(expired)

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      const event: WsEvent = {
        event_type: 'approval.expired',
        channel: 'approvals',
        timestamp: '2026-03-12T11:01:00Z',
        payload: { approval_id: 'approval-1', status: 'expired', action_type: 'deploy:production', risk_level: 'high' },
      }
      store.handleWsEvent(event)
      await flushPromises()

      expect(mockGetApproval).toHaveBeenCalledWith('approval-1')
      expect(store.approvals[0].status).toBe('expired')
    })

    it('does not duplicate approvals on repeated events', async () => {
      mockGetApproval.mockResolvedValue(mockApproval)

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      store.total = 1
      const event: WsEvent = {
        event_type: 'approval.submitted',
        channel: 'approvals',
        timestamp: '2026-03-12T10:00:00Z',
        payload: { approval_id: 'approval-1', status: 'pending', action_type: 'deploy:production', risk_level: 'high' },
      }
      store.handleWsEvent(event)
      await flushPromises()

      expect(store.approvals).toHaveLength(1)
      expect(store.total).toBe(1)
    })

    it('re-fetches filtered query on submit when filters active', async () => {
      mockListApprovals.mockResolvedValue({ data: [mockApproval], total: 1 })

      const store = useApprovalStore()
      await store.fetchApprovals({ status: 'pending' })
      expect(store.total).toBe(1)
      expect(store.approvals).toHaveLength(1)

      // After initial fetch, simulate a new item arriving via WS
      const newApproval = { ...mockApproval, id: 'approval-2' }
      mockListApprovals.mockResolvedValue({ data: [mockApproval, newApproval], total: 2 })

      const event: WsEvent = {
        event_type: 'approval.submitted',
        channel: 'approvals',
        timestamp: '2026-03-12T10:00:00Z',
        payload: { approval_id: 'approval-2', status: 'pending', action_type: 'deploy:production', risk_level: 'high' },
      }
      store.handleWsEvent(event)
      await flushPromises()

      // Should have re-fetched with filters — listApprovals called again
      expect(mockListApprovals).toHaveBeenCalledTimes(2)
      expect(mockListApprovals).toHaveBeenLastCalledWith({ status: 'pending' })
      expect(store.total).toBe(2)
      expect(store.approvals).toHaveLength(2)
    })

    it('re-fetches filtered query on status change when filters active', async () => {
      mockListApprovals.mockResolvedValue({ data: [mockApproval], total: 1 })

      const store = useApprovalStore()
      await store.fetchApprovals({ status: 'pending' })
      expect(store.total).toBe(1)

      // After approval, the pending filter should now return empty
      mockListApprovals.mockResolvedValue({ data: [], total: 0 })

      const event: WsEvent = {
        event_type: 'approval.approved',
        channel: 'approvals',
        timestamp: '2026-03-12T10:01:00Z',
        payload: { approval_id: 'approval-1', status: 'approved', action_type: 'deploy:production', risk_level: 'high' },
      }
      store.handleWsEvent(event)
      await flushPromises()

      // Should have re-fetched with filters
      expect(mockListApprovals).toHaveBeenCalledTimes(2)
      expect(store.total).toBe(0)
      expect(store.approvals).toHaveLength(0)
    })

    it('only decrements total when item was actually removed', async () => {
      const axiosError = new Error('Not found') as Error & { isAxiosError: boolean; response: { status: number } }
      axiosError.isAxiosError = true
      axiosError.response = { status: 404 }
      // Patch axios.isAxiosError to recognize our mock
      const originalIsAxiosError = (await import('axios')).default.isAxiosError
      vi.spyOn((await import('axios')).default, 'isAxiosError').mockImplementation((err) => {
        if (err === axiosError) return true
        return originalIsAxiosError(err)
      })

      mockGetApproval.mockRejectedValue(axiosError)

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      store.total = 5

      const event: WsEvent = {
        event_type: 'approval.approved',
        channel: 'approvals',
        timestamp: '2026-03-12T10:01:00Z',
        payload: { approval_id: 'approval-1', status: 'approved', action_type: 'deploy:production', risk_level: 'high' },
      }
      store.handleWsEvent(event)
      await flushPromises()

      // Item was found and removed, so total decremented
      expect(store.approvals).toHaveLength(0)
      expect(store.total).toBe(4)
    })

    it('does not decrement total when item was not in local list', async () => {
      const axiosError = new Error('Not found') as Error & { isAxiosError: boolean; response: { status: number } }
      axiosError.isAxiosError = true
      axiosError.response = { status: 404 }
      vi.spyOn((await import('axios')).default, 'isAxiosError').mockImplementation((err) => {
        if (err === axiosError) return true
        return false
      })

      mockGetApproval.mockRejectedValue(axiosError)

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      store.total = 5

      const event: WsEvent = {
        event_type: 'approval.expired',
        channel: 'approvals',
        timestamp: '2026-03-12T11:01:00Z',
        payload: { approval_id: 'approval-999', status: 'expired', action_type: 'deploy:production', risk_level: 'high' },
      }
      store.handleWsEvent(event)
      await flushPromises()

      // Item was not in local list, so total should not change
      expect(store.approvals).toHaveLength(1)
      expect(store.total).toBe(5)
    })

    it('does not change state on transient fetch errors', async () => {
      const networkError = new Error('Network error')
      mockGetApproval.mockRejectedValue(networkError)
      const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

      const store = useApprovalStore()
      store.approvals = [mockApproval]
      store.total = 1

      const event: WsEvent = {
        event_type: 'approval.approved',
        channel: 'approvals',
        timestamp: '2026-03-12T10:01:00Z',
        payload: { approval_id: 'approval-1', status: 'approved', action_type: 'deploy:production', risk_level: 'high' },
      }
      store.handleWsEvent(event)
      await flushPromises()

      // State unchanged on transient error
      expect(store.approvals).toHaveLength(1)
      expect(store.approvals[0].status).toBe('pending')
      expect(store.total).toBe(1)
      expect(warnSpy).toHaveBeenCalledWith('Failed to fetch approval:', 'approval-1', networkError)

      warnSpy.mockRestore()
    })
  })
})
