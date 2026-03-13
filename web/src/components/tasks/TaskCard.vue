<script setup lang="ts">
import StatusBadge from '@/components/common/StatusBadge.vue'
import type { Task } from '@/api/types'
import { formatRelativeTime } from '@/utils/format'

defineProps<{
  task: Task
}>()

defineEmits<{
  click: [task: Task]
}>()
</script>

<template>
  <div
    role="button"
    tabindex="0"
    class="cursor-pointer rounded-lg border border-slate-700 bg-slate-800 p-3 transition-colors hover:border-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500"
    @click="$emit('click', task)"
    @keydown.enter="$emit('click', task)"
    @keydown.space.prevent="$emit('click', task)"
  >
    <div class="mb-2 flex items-start justify-between gap-2">
      <p class="text-sm font-medium text-slate-200 line-clamp-2">{{ task.title }}</p>
      <StatusBadge :value="task.priority" type="priority" />
    </div>
    <div class="flex items-center justify-between text-xs text-slate-500">
      <span>{{ task.assigned_to ?? 'Unassigned' }}</span>
      <span>{{ formatRelativeTime(task.updated_at) }}</span>
    </div>
  </div>
</template>
