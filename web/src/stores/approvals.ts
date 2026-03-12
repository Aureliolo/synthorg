import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as approvalsApi from '@/api/endpoints/approvals'
import type { ApprovalItem, ApprovalFilters, ApproveRequest, RejectRequest, WsEvent } from '@/api/types'

export const useApprovalStore = defineStore('approvals', () => {
  const approvals = ref<ApprovalItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const pendingCount = computed(() => approvals.value.filter((a) => a.status === 'pending').length)

  async function fetchApprovals(filters?: ApprovalFilters) {
    loading.value = true
    error.value = null
    try {
      const result = await approvalsApi.listApprovals(filters)
      approvals.value = result.data
      total.value = result.total
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load approvals'
    } finally {
      loading.value = false
    }
  }

  async function approve(id: string, data?: ApproveRequest): Promise<ApprovalItem | null> {
    try {
      const updated = await approvalsApi.approveApproval(id, data)
      approvals.value = approvals.value.map((a) => (a.id === id ? updated : a))
      return updated
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to approve'
      return null
    }
  }

  async function reject(id: string, data: RejectRequest): Promise<ApprovalItem | null> {
    try {
      const updated = await approvalsApi.rejectApproval(id, data)
      approvals.value = approvals.value.map((a) => (a.id === id ? updated : a))
      return updated
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to reject'
      return null
    }
  }

  function handleWsEvent(event: WsEvent) {
    const payload = event.payload as Partial<ApprovalItem> & { id?: string }
    switch (event.event_type) {
      case 'approval.submitted':
        if (payload.id && !approvals.value.some((a) => a.id === payload.id)) {
          approvals.value = [payload as ApprovalItem, ...approvals.value]
          total.value++
        }
        break
      case 'approval.approved':
      case 'approval.rejected':
      case 'approval.expired':
        if (payload.id) {
          approvals.value = approvals.value.map((a) =>
            a.id === payload.id ? { ...a, ...payload } : a,
          )
        }
        break
    }
  }

  return {
    approvals,
    total,
    loading,
    error,
    pendingCount,
    fetchApprovals,
    approve,
    reject,
    handleWsEvent,
  }
})
