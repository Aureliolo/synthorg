<script setup lang="ts">
import { VueDraggable } from 'vue-draggable-plus'
import TaskCard from './TaskCard.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import type { Task, TaskStatus } from '@/api/types'

const props = defineProps<{
  status: TaskStatus
  tasks: Task[]
}>()

const emit = defineEmits<{
  'task-click': [task: Task]
  'task-added': [task: Task]
}>()

// vue-draggable-plus exposes the bound data via the internal _underlying_vm_ property.
// This is an undocumented API — if the library changes, this will need updating.
function handleAdd(event: { item: HTMLElement & { _underlying_vm_?: Task } }) {
  const task = event.item?._underlying_vm_
  if (task) {
    emit('task-added', task)
  } else {
    console.warn('KanbanColumn: could not resolve dragged task — _underlying_vm_ missing')
  }
}
</script>

<template>
  <div class="flex w-72 shrink-0 flex-col rounded-lg border border-slate-800 bg-slate-900">
    <div class="flex items-center justify-between border-b border-slate-800 px-3 py-2">
      <StatusBadge :value="status" />
      <span class="text-xs text-slate-500">{{ tasks.length }}</span>
    </div>
    <VueDraggable
      :model-value="tasks"
      group="tasks"
      item-key="id"
      class="flex-1 space-y-2 overflow-y-auto p-2"
      :style="{ minHeight: '100px' }"
      @add="handleAdd"
    >
      <template #item="{ element }">
        <TaskCard :task="element" @click="emit('task-click', element)" />
      </template>
    </VueDraggable>
  </div>
</template>
