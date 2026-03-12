<script setup lang="ts">
import StatusBadge from '@/components/common/StatusBadge.vue'
import { formatLabel } from '@/utils/format'

defineProps<{
  data: {
    label: string
    type: 'department' | 'team' | 'agent'
    status?: string
    role?: string
    seniority?: string
  }
}>()
</script>

<template>
  <div
    :class="[
      'rounded-lg border px-3 py-2 text-center',
      data.type === 'department'
        ? 'border-brand-600 bg-brand-600/10'
        : data.type === 'team'
          ? 'border-purple-600 bg-purple-600/10'
          : 'border-slate-700 bg-slate-800',
    ]"
  >
    <p class="text-sm font-medium text-slate-200">{{ data.label }}</p>
    <p v-if="data.role" class="text-xs text-slate-400">{{ data.role }}</p>
    <p v-if="data.seniority" class="text-xs text-slate-500">{{ formatLabel(data.seniority) }}</p>
    <StatusBadge v-if="data.status" :value="data.status" class="mt-1" />
    <span
      v-if="data.type === 'department'"
      class="mt-1 inline-block rounded bg-brand-600/20 px-1.5 py-0.5 text-xs text-brand-300"
    >
      {{ formatLabel(data.type) }}
    </span>
  </div>
</template>
