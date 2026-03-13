<script setup lang="ts">
import KanbanColumn from './KanbanColumn.vue'
import type { Task, TaskStatus } from '@/api/types'
import { TASK_STATUS_ORDER } from '@/utils/constants'

defineProps<{
  tasksByStatus: Partial<Record<TaskStatus, Task[]>>
}>()

defineEmits<{
  'task-click': [task: Task]
  'task-moved': [task: Task, targetStatus: TaskStatus]
}>()
</script>

<template>
  <div class="flex gap-4 overflow-x-auto pb-4">
    <KanbanColumn
      v-for="status in TASK_STATUS_ORDER"
      :key="status"
      :status="status"
      :tasks="tasksByStatus[status] ?? []"
      @task-click="$emit('task-click', $event)"
      @task-added="$emit('task-moved', $event, status)"
    />
  </div>
</template>
