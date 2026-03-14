import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'
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

  /**
   * Handle a WebSocket approval event.
   *
   * The backend sends a minimal payload with ``approval_id`` (not ``id``).
   * For new submissions, we fetch the full item from the API.
   * For status changes, we update the local status and re-fetch for
   * the complete updated item.
   *
   * This function is synchronous to satisfy the ``WsEventHandler`` type
   * contract.  Async work runs inside a void IIFE.
   */
  function handleWsEvent(event: WsEvent): void {
    const payload = event.payload as Record<string, unknown> | null
    if (!payload || typeof payload !== 'object') return
    const approvalId = payload.approval_id
    if (typeof approvalId !== 'string' || !approvalId) return

    void (async () => {
      try {
        switch (event.event_type) {
          case 'approval.submitted':
            if (!approvals.value.some((a) => a.id === approvalId)) {
              if (activeFilters.value) {
                // Filters active — re-fetch the filtered query to stay consistent
                await fetchApprovals(activeFilters.value)
              } else {
                try {
                  const item = await approvalsApi.getApproval(approvalId)
                  // Re-check after async fetch to prevent duplicate insertion
                  if (!approvals.value.some((a) => a.id === approvalId)) {
                    approvals.value = [item, ...approvals.value]
                    total.value++
                  }
                } catch (err) {
                  if (axios.isAxiosError(err) && (err.response?.status === 404 || err.response?.status === 410)) {
                    // Item genuinely gone — skip
                  } else {
                    console.warn('Failed to fetch approval:', approvalId, err)
                  }
                }
              }
            }
            break
          case 'approval.approved':
          case 'approval.rejected':
          case 'approval.expired':
            if (activeFilters.value) {
              // Filters active — re-fetch to reconcile (items may enter/leave the filtered set)
              await fetchApprovals(activeFilters.value)
            } else {
              try {
                const updated = await approvalsApi.getApproval(approvalId)
                approvals.value = approvals.value.map((a) =>
                  a.id === approvalId ? updated : a,
                )
              } catch (err) {
                if (axios.isAxiosError(err) && (err.response?.status === 404 || err.response?.status === 410)) {
                  // Item genuinely gone — remove from local list
                  const lengthBefore = approvals.value.length
                  approvals.value = approvals.value.filter((a) => a.id !== approvalId)
                  const removed = lengthBefore - approvals.value.length
                  if (removed > 0) {
                    total.value = Math.max(0, total.value - removed)
                  }
                } else {
                  console.warn('Failed to fetch approval:', approvalId, err)
                }
              }
            }
            break
        }
      } catch (err) {
        console.warn('Unexpected error in WS event handler:', err)
      }
    })()
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
