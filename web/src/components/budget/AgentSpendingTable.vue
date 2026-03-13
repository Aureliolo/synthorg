<script setup lang="ts">
import { computed } from 'vue'
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import type { CostRecord } from '@/api/types'
import { formatCurrency } from '@/utils/format'

const props = defineProps<{
  records: CostRecord[]
}>()

interface AgentSpendingSummary {
  agent_id: string
  total_cost_usd: number
  record_count: number
  total_input_tokens: number
  total_output_tokens: number
}

const agentSummaries = computed<AgentSpendingSummary[]>(() => {
  const map = new Map<string, AgentSpendingSummary>()
  for (const r of props.records) {
    const existing = map.get(r.agent_id)
    if (existing) {
      map.set(r.agent_id, {
        ...existing,
        total_cost_usd: existing.total_cost_usd + r.cost_usd,
        record_count: existing.record_count + 1,
        total_input_tokens: existing.total_input_tokens + r.input_tokens,
        total_output_tokens: existing.total_output_tokens + r.output_tokens,
      })
    } else {
      map.set(r.agent_id, {
        agent_id: r.agent_id,
        total_cost_usd: r.cost_usd,
        record_count: 1,
        total_input_tokens: r.input_tokens,
        total_output_tokens: r.output_tokens,
      })
    }
  }
  return Array.from(map.values()).sort((a, b) => b.total_cost_usd - a.total_cost_usd)
})
</script>

<template>
  <DataTable :value="agentSummaries" striped-rows class="text-sm">
    <Column field="agent_id" header="Agent" sortable />
    <Column field="total_cost_usd" header="Total Cost" sortable style="width: 120px">
      <template #body="{ data }">
        {{ formatCurrency(data.total_cost_usd) }}
      </template>
    </Column>
    <Column field="record_count" header="Calls" sortable style="width: 80px" />
    <Column field="total_input_tokens" header="Input Tokens" sortable style="width: 120px" />
    <Column field="total_output_tokens" header="Output Tokens" sortable style="width: 120px" />
  </DataTable>
</template>
