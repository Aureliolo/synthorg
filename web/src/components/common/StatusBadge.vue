<script setup lang="ts">
import Tag from 'primevue/tag'
import { statusColors, priorityColors, riskColors } from '@/styles/theme'
import { formatLabel } from '@/utils/format'

const props = defineProps<{
  value: string
  type?: 'status' | 'priority' | 'risk'
}>()

function getColorClass(): string {
  switch (props.type) {
    case 'priority':
      return priorityColors[props.value] ?? 'bg-slate-600 text-slate-200'
    case 'risk':
      return riskColors[props.value] ?? 'bg-slate-600 text-slate-200'
    default:
      return statusColors[props.value] ?? 'bg-slate-600 text-slate-200'
  }
}
</script>

<template>
  <Tag :class="['text-xs font-medium', getColorClass()]">
    {{ formatLabel(value) }}
  </Tag>
</template>
