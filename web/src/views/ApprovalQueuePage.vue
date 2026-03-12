<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Sidebar from 'primevue/sidebar'
import Dropdown from 'primevue/dropdown'
import { useToast } from 'primevue/usetoast'
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import ApprovalDetail from '@/components/approvals/ApprovalDetail.vue'
import ApprovalActions from '@/components/approvals/ApprovalActions.vue'
import { useApprovalStore } from '@/stores/approvals'
import { useWebSocketStore } from '@/stores/websocket'
import { useAuthStore } from '@/stores/auth'
import { formatDate } from '@/utils/format'
import type { ApprovalItem, ApprovalStatus } from '@/api/types'

const toast = useToast()
const approvalStore = useApprovalStore()
const wsStore = useWebSocketStore()
const authStore = useAuthStore()

const selected = ref<ApprovalItem | null>(null)
const detailVisible = ref(false)
const statusFilter = ref<ApprovalStatus | undefined>(undefined)

const statusOptions = [
  { label: 'Pending', value: 'pending' },
  { label: 'Approved', value: 'approved' },
  { label: 'Rejected', value: 'rejected' },
  { label: 'Expired', value: 'expired' },
]

onMounted(async () => {
  if (authStore.token && !wsStore.connected) {
    wsStore.connect(authStore.token)
  }
  wsStore.subscribe(['approvals'])
  wsStore.onChannelEvent('approvals', approvalStore.handleWsEvent)
  await approvalStore.fetchApprovals()
})

onUnmounted(() => {
  wsStore.offChannelEvent('approvals', approvalStore.handleWsEvent)
})

function openDetail(approval: ApprovalItem) {
  selected.value = approval
  detailVisible.value = true
}

async function handleApprove(id: string, comment: string) {
  const result = await approvalStore.approve(id, comment ? { comment } : undefined)
  if (result) {
    selected.value = result
    toast.add({ severity: 'success', summary: 'Approved', life: 3000 })
  } else {
    toast.add({ severity: 'error', summary: approvalStore.error ?? 'Approve failed', life: 5000 })
  }
}

async function handleReject(id: string, reason: string) {
  const result = await approvalStore.reject(id, { reason })
  if (result) {
    selected.value = result
    toast.add({ severity: 'info', summary: 'Rejected', life: 3000 })
  } else {
    toast.add({ severity: 'error', summary: approvalStore.error ?? 'Reject failed', life: 5000 })
  }
}

function filterByStatus() {
  approvalStore.fetchApprovals({ status: statusFilter.value })
}
</script>

<template>
  <AppShell>
    <PageHeader title="Approval Queue" subtitle="Review and decide on pending approval requests">
      <template #actions>
        <Dropdown
          v-model="statusFilter"
          :options="statusOptions"
          option-label="label"
          option-value="value"
          placeholder="All Statuses"
          show-clear
          class="w-40"
          @change="filterByStatus"
        />
      </template>
    </PageHeader>

    <ErrorBoundary :error="approvalStore.error" @retry="approvalStore.fetchApprovals()">
      <LoadingSkeleton v-if="approvalStore.loading && approvalStore.approvals.length === 0" :lines="6" />
      <DataTable
        v-else
        :value="approvalStore.approvals"
        :total-records="approvalStore.total"
        :loading="approvalStore.loading"
        striped-rows
        row-hover
        class="text-sm"
        @row-click="openDetail($event.data)"
      >
        <Column field="title" header="Title" sortable />
        <Column field="status" header="Status" sortable style="width: 120px">
          <template #body="{ data }">
            <StatusBadge :value="data.status" />
          </template>
        </Column>
        <Column field="risk_level" header="Risk" sortable style="width: 100px">
          <template #body="{ data }">
            <StatusBadge :value="data.risk_level" type="risk" />
          </template>
        </Column>
        <Column field="requested_by" header="Requested By" style="width: 150px" />
        <Column field="action_type" header="Action" style="width: 140px" />
        <Column field="created_at" header="Created" sortable style="width: 160px">
          <template #body="{ data }">
            <span class="text-slate-400">{{ formatDate(data.created_at) }}</span>
          </template>
        </Column>
      </DataTable>
    </ErrorBoundary>

    <Sidebar :visible="detailVisible" position="right" class="w-[480px]" @update:visible="detailVisible = $event">
      <template #header>
        <span class="text-lg font-semibold text-slate-100">Approval Details</span>
      </template>
      <div v-if="selected" class="space-y-6">
        <ApprovalDetail :approval="selected" />
        <ApprovalActions
          :approval-id="selected.id"
          :status="selected.status"
          @approve="handleApprove"
          @reject="handleReject"
        />
      </div>
    </Sidebar>
  </AppShell>
</template>
