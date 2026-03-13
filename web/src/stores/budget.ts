import { defineStore } from 'pinia'
import { ref } from 'vue'
import * as budgetApi from '@/api/endpoints/budget'
import { getErrorMessage } from '@/utils/errors'
import type { BudgetConfig, CostRecord, AgentSpending, WsEvent } from '@/api/types'

const MAX_WS_RECORDS = 500

/** Runtime type guard for CostRecord-shaped payloads — validates all required fields. */
function isCostRecord(payload: unknown): payload is CostRecord {
  if (typeof payload !== 'object' || payload === null) return false
  const p = payload as Record<string, unknown>
  return (
    typeof p.agent_id === 'string' &&
    typeof p.task_id === 'string' &&
    typeof p.provider === 'string' &&
    typeof p.model === 'string' &&
    typeof p.cost_usd === 'number' &&
    typeof p.input_tokens === 'number' &&
    typeof p.output_tokens === 'number' &&
    typeof p.timestamp === 'string'
  )
}

export const useBudgetStore = defineStore('budget', () => {
  const config = ref<BudgetConfig | null>(null)
  const records = ref<CostRecord[]>([])
  const totalRecords = ref(0)
  const loading = ref(false)
  const error = ref<string | null>(null)
  let lastFetchParams: { agent_id?: string; task_id?: string; limit?: number } | undefined

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
    lastFetchParams = params ? { ...params } : undefined
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
        // Skip if active filters don't match this record
        if (lastFetchParams?.agent_id && event.payload.agent_id !== lastFetchParams.agent_id) return
        if (lastFetchParams?.task_id && event.payload.task_id !== lastFetchParams.task_id) return
        const limit = lastFetchParams?.limit ?? MAX_WS_RECORDS
        records.value = [event.payload, ...records.value].slice(0, limit)
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
