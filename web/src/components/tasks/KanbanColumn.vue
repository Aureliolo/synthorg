<script setup lang="ts">
import { VueDraggable, type DraggableEvent } from 'vue-draggable-plus'
import TaskCard from './TaskCard.vue'
import StatusBadge from '@/components/common/StatusBadge.vue'
import type { Task } from '@/api/types'

const props = defineProps<{
  status: string
  tasks: Task[]
}>()

const emit = defineEmits<{
  'task-click': [task: Task]
  'task-moved': [task: Task, targetStatus: string]
}>()

function handleDragEnd(event: DraggableEvent<Task>) {
  const el = event.item as HTMLElement & { _underlying_vm_?: Task }
  const task = el?._underlying_vm_
  if (task) {
    emit('task-moved', task, props.status)
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
      @end="handleDragEnd"
    >
      <template #item="{ element }">
        <TaskCard :task="element" @click="emit('task-click', element)" />
      </template>
    </VueDraggable>
  </div>
</template>
