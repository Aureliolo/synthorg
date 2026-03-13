<script setup lang="ts">
import Tag from 'primevue/tag'
import { statusColors, priorityColors, riskColors, type Status, type Priority, type RiskLevel } from '@/styles/theme'
import { formatLabel } from '@/utils/format'

const FALLBACK = 'bg-slate-600 text-slate-200'

const props = defineProps<{
  value: string
  type?: 'status' | 'priority' | 'risk'
}>()

function getColorClass(): string {
  switch (props.type) {
    case 'priority':
      return priorityColors[props.value as Priority] ?? FALLBACK
    case 'risk':
      return riskColors[props.value as RiskLevel] ?? FALLBACK
    default:
      return statusColors[props.value as Status] ?? FALLBACK
  }
}
</script>

<template>
  <Tag :class="['text-xs font-medium', getColorClass()]">
    {{ formatLabel(value) }}
  </Tag>
</template>
