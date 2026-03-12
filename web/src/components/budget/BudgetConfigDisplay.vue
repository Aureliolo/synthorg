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
      <p class="text-xs text-slate-500">Daily Limit</p>
      <p class="text-sm font-medium text-slate-200">{{ formatCurrency(config.daily_limit_usd) }}</p>
    </div>
    <div>
      <p class="text-xs text-slate-500">Monthly Limit</p>
      <p class="text-sm font-medium text-slate-200">{{ formatCurrency(config.monthly_limit_usd) }}</p>
    </div>
    <div>
      <p class="text-xs text-slate-500">Per Task Limit</p>
      <p class="text-sm font-medium text-slate-200">{{ formatCurrency(config.per_task_limit_usd) }}</p>
    </div>
    <div>
      <p class="text-xs text-slate-500">Per Agent Limit</p>
      <p class="text-sm font-medium text-slate-200">{{ formatCurrency(config.per_agent_limit_usd) }}</p>
    </div>
    <div>
      <p class="text-xs text-slate-500">Alert Threshold</p>
      <p class="text-sm font-medium text-slate-200">{{ config.alert_threshold_percent }}%</p>
    </div>
  </div>
</template>
