<script setup lang="ts">
import Dropdown from 'primevue/dropdown'
import Button from 'primevue/button'
import type { TaskStatus, TaskFilters as TaskFilterType } from '@/api/types'
import { TASK_STATUS_ORDER } from '@/utils/constants'
import { formatLabel } from '@/utils/format'

defineProps<{
  agents: string[]
  filters: TaskFilterType
}>()

const emit = defineEmits<{
  update: [filters: Partial<TaskFilterType>]
  reset: []
}>()

const statusOptions = TASK_STATUS_ORDER.map((s) => ({ label: formatLabel(s), value: s }))

function updateFilter<K extends keyof TaskFilterType>(key: K, value: TaskFilterType[K] | undefined) {
  emit('update', { [key]: value ?? undefined } as Partial<TaskFilterType>)
}
</script>

<template>
  <div class="flex flex-wrap items-center gap-3">
    <Dropdown
      :model-value="filters.status"
      :options="statusOptions"
      option-label="label"
      option-value="value"
      placeholder="Status"
      show-clear
      class="w-40"
      @update:model-value="updateFilter('status', $event)"
    />
    <Dropdown
      :model-value="filters.assigned_to"
      :options="agents"
      placeholder="Assignee"
      show-clear
      class="w-40"
      @update:model-value="updateFilter('assigned_to', $event)"
    />
    <Button label="Reset" icon="pi pi-filter-slash" text size="small" @click="$emit('reset')" />
  </div>
</template>
