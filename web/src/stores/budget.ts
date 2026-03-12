import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as budgetApi from '@/api/endpoints/budget'
import type { BudgetConfig, CostRecord, AgentSpending, WsEvent } from '@/api/types'

export const useBudgetStore = defineStore('budget', () => {
  const config = ref<BudgetConfig | null>(null)
  const records = ref<CostRecord[]>([])
  const totalRecords = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchConfig() {
    try {
      config.value = await budgetApi.getBudgetConfig()
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load budget config'
    }
  }

  async function fetchRecords(params?: { agent_id?: string; task_id?: string; limit?: number }) {
    loading.value = true
    error.value = null
    try {
      const result = await budgetApi.listCostRecords(params)
      records.value = result.data
      totalRecords.value = result.total
    } catch (err) {
      error.value = err instanceof Error ? err.message : 'Failed to load cost records'
    } finally {
      loading.value = false
    }
  }

  async function fetchAgentSpending(agentId: string): Promise<AgentSpending | null> {
    try {
      return await budgetApi.getAgentSpending(agentId)
    } catch {
      return null
    }
  }

  function handleWsEvent(event: WsEvent) {
    if (event.event_type === 'budget.record_added') {
      const record = event.payload as unknown as CostRecord
      if (record.id) {
        records.value = [record, ...records.value]
        totalRecords.value++
      }
    }
  }

  return {
    config,
    records,
    totalRecords,
    loading,
    error,
    fetchConfig,
    fetchRecords,
    fetchAgentSpending,
    handleWsEvent,
  }
})
