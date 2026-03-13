<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import BudgetConfigDisplay from '@/components/budget/BudgetConfigDisplay.vue'
import SpendingChart from '@/components/budget/SpendingChart.vue'
import AgentSpendingTable from '@/components/budget/AgentSpendingTable.vue'
import { useBudgetStore } from '@/stores/budget'
import { useWebSocketStore } from '@/stores/websocket'
import { useAuthStore } from '@/stores/auth'

import { sanitizeForLog } from '@/utils/logging'

const budgetStore = useBudgetStore()
const wsStore = useWebSocketStore()
const authStore = useAuthStore()

onMounted(async () => {
  try {
    if (authStore.token && !wsStore.connected) {
      wsStore.connect(authStore.token)
    }
    wsStore.subscribe(['budget'])
    wsStore.onChannelEvent('budget', budgetStore.handleWsEvent)
  } catch (err) {
    console.error('WebSocket setup failed:', sanitizeForLog(err))
  }
  try {
    await Promise.all([budgetStore.fetchConfig(), budgetStore.fetchRecords({ limit: 200 })])
  } catch (err) {
    console.error('Initial data fetch failed:', sanitizeForLog(err))
  }
})

onUnmounted(() => {
  wsStore.unsubscribe(['budget'])
  wsStore.offChannelEvent('budget', budgetStore.handleWsEvent)
})

async function retryFetch() {
  try {
    await Promise.all([budgetStore.fetchConfig(), budgetStore.fetchRecords({ limit: 200 })])
  } catch (err) {
    console.error('Budget data fetch failed:', sanitizeForLog(err))
  }
}
</script>

<template>
  <AppShell>
    <PageHeader title="Budget" subtitle="Monitor spending and cost allocation" />

    <ErrorBoundary :error="budgetStore.error" @retry="retryFetch">
      <LoadingSkeleton v-if="budgetStore.loading && budgetStore.records.length === 0" :lines="6" />
      <template v-else>
        <div class="space-y-6">
          <BudgetConfigDisplay :config="budgetStore.config" />

          <div class="rounded-lg border border-slate-800 bg-slate-900 p-5">
            <h3 class="mb-4 text-sm font-medium text-slate-300">Daily Spending</h3>
            <SpendingChart :records="budgetStore.records" />
          </div>

          <div class="rounded-lg border border-slate-800 bg-slate-900 p-5">
            <h3 class="mb-4 text-sm font-medium text-slate-300">Agent Spending</h3>
            <AgentSpendingTable :records="budgetStore.records" />
          </div>
        </div>
      </template>
    </ErrorBoundary>
  </AppShell>
</template>
