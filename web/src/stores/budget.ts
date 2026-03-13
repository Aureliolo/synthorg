import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as budgetApi from '@/api/endpoints/budget'
import { getErrorMessage } from '@/utils/errors'
import type { BudgetConfig, CostRecord, AgentSpending, WsEvent } from '@/api/types'

const MAX_WS_RECORDS = 500

/** Runtime type guard for CostRecord-shaped payloads. */
function isCostRecord(payload: unknown): payload is CostRecord {
  if (typeof payload !== 'object' || payload === null) return false
  const p = payload as Record<string, unknown>
  return typeof p.agent_id === 'string' && typeof p.cost_usd === 'number'
}

export const useBudgetStore = defineStore('budget', () => {
  const config = ref<BudgetConfig | null>(null)
  const records = ref<CostRecord[]>([])
  const totalRecords = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)

  async function fetchConfig() {
    loading.value = true
    error.value = null
    try {
      config.value = await budgetApi.getBudgetConfig()
    } catch (err) {
      error.value = getErrorMessage(err)
    } finally {
      loading.value = false
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
      error.value = getErrorMessage(err)
    } finally {
      loading.value = false
    }
  }

  async function fetchAgentSpending(agentId: string): Promise<AgentSpending | null> {
    loading.value = true
    error.value = null
    try {
      return await budgetApi.getAgentSpending(agentId)
    } catch (err) {
      error.value = getErrorMessage(err)
      return null
    } finally {
      loading.value = false
    }
  }

  function handleWsEvent(event: WsEvent) {
    if (event.event_type === 'budget.record_added') {
      if (isCostRecord(event.payload)) {
        records.value = [event.payload, ...records.value].slice(0, MAX_WS_RECORDS)
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
