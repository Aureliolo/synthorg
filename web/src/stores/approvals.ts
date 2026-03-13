import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as approvalsApi from '@/api/endpoints/approvals'
import { getErrorMessage } from '@/utils/errors'
import type { ApprovalItem, ApprovalFilters, ApproveRequest, RejectRequest, WsEvent } from '@/api/types'

export const useApprovalStore = defineStore('approvals', () => {
  const approvals = ref<ApprovalItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const activeFilters = ref<ApprovalFilters | undefined>(undefined)

  const pendingCount = computed(() => approvals.value.filter((a) => a.status === 'pending').length)

  async function fetchApprovals(filters?: ApprovalFilters) {
    loading.value = true
    error.value = null
    activeFilters.value = filters ? { ...filters } : undefined
    try {
      const result = await approvalsApi.listApprovals(filters)
      approvals.value = result.data
      total.value = result.total
    } catch (err) {
      error.value = getErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function approve(id: string, data?: ApproveRequest): Promise<ApprovalItem | null> {
    error.value = null
    try {
      const updated = await approvalsApi.approveApproval(id, data)
      approvals.value = approvals.value.map((a) => (a.id === id ? updated : a))
      return updated
    } catch (err) {
      error.value = getErrorMessage(err)
      return null
    }
  }

  async function reject(id: string, data: RejectRequest): Promise<ApprovalItem | null> {
    error.value = null
    try {
      const updated = await approvalsApi.rejectApproval(id, data)
      approvals.value = approvals.value.map((a) => (a.id === id ? updated : a))
      return updated
    } catch (err) {
      error.value = getErrorMessage(err)
      return null
    }
  }

  /** Runtime check for required ApprovalItem fields before insertion. */
  function isValidApprovalPayload(p: Record<string, unknown>): boolean {
    return (
      typeof p.id === 'string' && p.id !== '' &&
      typeof p.action_type === 'string' &&
      typeof p.title === 'string' &&
      typeof p.status === 'string' &&
      typeof p.requested_by === 'string' &&
      typeof p.risk_level === 'string' &&
      typeof p.created_at === 'string'
    )
  }

  function handleWsEvent(event: WsEvent) {
    const payload = event.payload as Record<string, unknown> | null
    if (!payload || typeof payload !== 'object') return
    switch (event.event_type) {
      case 'approval.submitted':
        if (
          isValidApprovalPayload(payload) &&
          !approvals.value.some((a) => a.id === payload.id)
        ) {
          // Only insert + count into unfiltered views to keep list consistent
          if (!activeFilters.value) {
            approvals.value = [payload as unknown as ApprovalItem, ...approvals.value]
            total.value++
          }
        }
        break
      case 'approval.approved':
      case 'approval.rejected':
      case 'approval.expired':
        if (typeof payload.id === 'string' && payload.id) {
          approvals.value = approvals.value.map((a) =>
            a.id === payload.id ? { ...a, ...(payload as Partial<ApprovalItem>) } : a,
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
