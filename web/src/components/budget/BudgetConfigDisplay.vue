<script setup lang="ts">
import type { BudgetConfig } from '@/api/types'
import { formatCurrency } from '@/utils/format'

defineProps<{
  config: BudgetConfig | null
}>()
</script>

<template>
  <div v-if="config" class="grid grid-cols-2 gap-4 rounded-lg border border-slate-800 p-4 sm:grid-cols-5">
    <div>
      <p class="text-xs text-slate-500">Monthly Budget</p>
      <p class="text-sm font-medium text-slate-200">{{ formatCurrency(config.total_monthly) }}</p>
    </div>
    <div>
      <p class="text-xs text-slate-500">Per Agent Daily</p>
      <p class="text-sm font-medium text-slate-200">{{ formatCurrency(config.per_agent_daily_limit) }}</p>
    </div>
    <div>
      <p class="text-xs text-slate-500">Per Task Limit</p>
      <p class="text-sm font-medium text-slate-200">{{ formatCurrency(config.per_task_limit) }}</p>
    </div>
    <div>
      <p class="text-xs text-slate-500">Alert Threshold</p>
      <p class="text-sm font-medium text-slate-200">{{ config.alerts.warn_at }}%</p>
    </div>
    <div>
      <p class="text-xs text-slate-500">Reset Day</p>
      <p class="text-sm font-medium text-slate-200">{{ config.reset_day }}</p>
    </div>
  </div>
</template>
