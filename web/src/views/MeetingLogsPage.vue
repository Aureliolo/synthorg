<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import Sidebar from 'primevue/sidebar'
import Dropdown from 'primevue/dropdown'
import Button from 'primevue/button'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import { useToast } from 'primevue/usetoast'
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import { useMeetingStore } from '@/stores/meetings'
import { useWebSocketStore } from '@/stores/websocket'
import { useAuthStore } from '@/stores/auth'
import { useAuth } from '@/composables/useAuth'
import { sanitizeForLog } from '@/utils/logging'
import type { MeetingRecord, MeetingStatus } from '@/api/types'

const toast = useToast()
const meetingStore = useMeetingStore()
const wsStore = useWebSocketStore()
const authStore = useAuthStore()
const { canWrite } = useAuth()

const selected = computed(() => meetingStore.selectedMeeting)
const detailVisible = ref(false)
const statusFilter = ref<MeetingStatus | undefined>(undefined)
const triggerDialogVisible = ref(false)
const triggerEventName = ref('')
const triggerLoading = ref(false)

const statusOptions = [
  { label: 'Scheduled', value: 'scheduled' },
  { label: 'In Progress', value: 'in_progress' },
  { label: 'Completed', value: 'completed' },
  { label: 'Failed', value: 'failed' },
  { label: 'Cancelled', value: 'cancelled' },
  { label: 'Budget Exhausted', value: 'budget_exhausted' },
]

onMounted(async () => {
  try {
    if (authStore.token && !wsStore.connected) {
      wsStore.connect(authStore.token)
    }
    wsStore.subscribe(['meetings'])
    wsStore.onChannelEvent('meetings', meetingStore.handleWsEvent)
  } catch (err) {
    console.error('WebSocket setup failed:', sanitizeForLog(err))
  }
  try {
    await meetingStore.fetchMeetings()
  } catch (err) {
    console.error('Initial data fetch failed:', sanitizeForLog(err))
  }
})

onUnmounted(() => {
  wsStore.unsubscribe(['meetings'])
  wsStore.offChannelEvent('meetings', meetingStore.handleWsEvent)
})

function openDetail(meeting: MeetingRecord) {
  meetingStore.selectedMeeting = meeting
  detailVisible.value = true
}

async function filterByStatus() {
  try {
    await meetingStore.fetchMeetings({ status: statusFilter.value })
  } catch (err) {
    console.error('Filter fetch failed:', sanitizeForLog(err))
    toast.add({ severity: 'error', summary: 'Failed to apply filter', life: 5000 })
  }
}

async function handleTrigger() {
  if (!triggerEventName.value.trim()) return
  triggerLoading.value = true
  try {
    const result = await meetingStore.triggerMeeting({
      event_name: triggerEventName.value.trim(),
    })
    if (result && result.length > 0) {
      toast.add({
        severity: 'success',
        summary: `Triggered ${result.length} meeting(s)`,
        life: 3000,
      })
      triggerDialogVisible.value = false
      triggerEventName.value = ''
    } else if (result && result.length === 0) {
      toast.add({
        severity: 'info',
        summary: 'No matching meeting types',
        life: 3000,
      })
      triggerDialogVisible.value = false
      triggerEventName.value = ''
    } else {
      toast.add({
        severity: 'error',
        summary: meetingStore.error ?? 'Trigger failed',
        life: 5000,
      })
    }
  } catch (err) {
    console.error('Trigger failed:', sanitizeForLog(err))
    toast.add({ severity: 'error', summary: 'Trigger failed', life: 5000 })
  } finally {
    triggerLoading.value = false
  }
}

function formatTokens(record: MeetingRecord): string {
  if (record.minutes) {
    return `${record.minutes.total_tokens.toLocaleString()} / ${record.token_budget.toLocaleString()}`
  }
  return `- / ${record.token_budget.toLocaleString()}`
}
</script>

<template>
  <AppShell>
    <PageHeader title="Meeting Logs" subtitle="View agent meeting transcripts and outcomes">
      <template #actions>
        <Dropdown
          v-model="statusFilter"
          :options="statusOptions"
          option-label="label"
          option-value="value"
          placeholder="All Statuses"
          show-clear
          class="w-44"
          aria-label="Filter by status"
          @change="filterByStatus"
        />
        <Button
          v-if="canWrite"
          label="Trigger Meeting"
          icon="pi pi-play"
          severity="secondary"
          size="small"
          @click="triggerDialogVisible = true"
        />
      </template>
    </PageHeader>

    <ErrorBoundary :error="meetingStore.error" @retry="() => meetingStore.fetchMeetings()">
      <LoadingSkeleton v-if="meetingStore.loading && meetingStore.meetings.length === 0" :lines="6" />
      <DataTable
        v-else
        :value="meetingStore.meetings"
        :total-records="meetingStore.total"
        :loading="meetingStore.loading"
        striped-rows
        row-hover
        class="text-sm"
        @row-click="openDetail($event.data)"
      >
        <Column field="meeting_type_name" header="Type" sortable />
        <Column field="protocol_type" header="Protocol" sortable class="w-[150px]" />
        <Column field="status" header="Status" sortable class="w-[140px]">
          <template #body="{ data }">
            <StatusBadge :value="data.status" />
          </template>
        </Column>
        <Column header="Tokens" class="w-[160px]">
          <template #body="{ data }">
            <span class="text-slate-400 font-mono text-xs">{{ formatTokens(data) }}</span>
          </template>
        </Column>
        <Column field="meeting_id" header="ID" class="w-[140px]">
          <template #body="{ data }">
            <span class="text-slate-500 font-mono text-xs">{{ data.meeting_id }}</span>
          </template>
        </Column>
      </DataTable>
    </ErrorBoundary>

    <!-- Detail sidebar -->
    <Sidebar :visible="detailVisible" position="right" class="w-[560px]" @update:visible="detailVisible = $event">
      <template #header>
        <span class="text-lg font-semibold text-slate-100">Meeting Details</span>
      </template>
      <div v-if="selected" class="space-y-4">
        <div class="grid grid-cols-2 gap-3 text-sm">
          <div>
            <span class="text-slate-500">Type</span>
            <p class="text-slate-200">{{ selected.meeting_type_name }}</p>
          </div>
          <div>
            <span class="text-slate-500">Protocol</span>
            <p class="text-slate-200">{{ selected.protocol_type }}</p>
          </div>
          <div>
            <span class="text-slate-500">Status</span>
            <p><StatusBadge :value="selected.status" /></p>
          </div>
          <div>
            <span class="text-slate-500">Token Budget</span>
            <p class="text-slate-200">{{ selected.token_budget.toLocaleString() }}</p>
          </div>
        </div>

        <div v-if="selected.error_message" class="rounded-lg bg-red-900/20 border border-red-800 p-3">
          <p class="text-sm text-red-300">{{ selected.error_message }}</p>
        </div>

        <!-- Minutes detail -->
        <template v-if="selected.minutes">
          <div class="border-t border-slate-700 pt-3">
            <h3 class="text-sm font-semibold text-slate-300 mb-2">Summary</h3>
            <p class="text-sm text-slate-400 whitespace-pre-wrap">{{ selected.minutes.summary || 'No summary' }}</p>
          </div>

          <div v-if="selected.minutes.decisions.length > 0" class="border-t border-slate-700 pt-3">
            <h3 class="text-sm font-semibold text-slate-300 mb-2">Decisions</h3>
            <ul class="list-disc list-inside text-sm text-slate-400 space-y-1">
              <li v-for="(decision, i) in selected.minutes.decisions" :key="i">{{ decision }}</li>
            </ul>
          </div>

          <div v-if="selected.minutes.action_items.length > 0" class="border-t border-slate-700 pt-3">
            <h3 class="text-sm font-semibold text-slate-300 mb-2">Action Items</h3>
            <div v-for="(item, i) in selected.minutes.action_items" :key="i" class="mb-2 p-2 bg-slate-800 rounded text-sm">
              <p class="text-slate-200">{{ item.description }}</p>
              <div class="flex gap-3 mt-1 text-xs text-slate-500">
                <span v-if="item.assignee_id">Assignee: {{ item.assignee_id }}</span>
                <StatusBadge :value="item.priority" type="priority" />
              </div>
            </div>
          </div>

          <div v-if="selected.minutes.contributions.length > 0" class="border-t border-slate-700 pt-3">
            <h3 class="text-sm font-semibold text-slate-300 mb-2">
              Contributions ({{ selected.minutes.contributions.length }})
            </h3>
            <div
              v-for="(contrib, i) in selected.minutes.contributions"
              :key="i"
              class="mb-3 p-2 bg-slate-800 rounded text-sm"
            >
              <div class="flex items-center justify-between mb-1">
                <span class="text-brand-400 font-medium">{{ contrib.agent_id }}</span>
                <span class="text-xs text-slate-500">{{ contrib.phase }} #{{ contrib.turn_number }}</span>
              </div>
              <p class="text-slate-300 whitespace-pre-wrap">{{ contrib.content }}</p>
              <div class="text-xs text-slate-600 mt-1">
                {{ contrib.input_tokens + contrib.output_tokens }} tokens
              </div>
            </div>
          </div>
        </template>
      </div>
    </Sidebar>

    <!-- Trigger dialog -->
    <Dialog v-model:visible="triggerDialogVisible" header="Trigger Event Meeting" :modal="true" class="w-96">
      <div class="space-y-4">
        <div>
          <label for="eventName" class="block text-sm text-slate-400 mb-1">Event Name</label>
          <InputText
            id="eventName"
            v-model="triggerEventName"
            placeholder="e.g. code_review_complete"
            class="w-full"
            @keyup.enter="handleTrigger"
          />
        </div>
      </div>
      <template #footer>
        <Button label="Cancel" severity="secondary" text @click="triggerDialogVisible = false" />
        <Button
          label="Trigger"
          icon="pi pi-play"
          :loading="triggerLoading"
          :disabled="!triggerEventName.trim()"
          @click="handleTrigger"
        />
      </template>
    </Dialog>
  </AppShell>
</template>
