<script setup lang="ts">
import DataTable from 'primevue/datatable'
import Column from 'primevue/column'
import StatusBadge from '@/components/common/StatusBadge.vue'
import type { Task } from '@/api/types'
import { formatDate } from '@/utils/format'
import { DEFAULT_PAGE_SIZE } from '@/utils/constants'

defineProps<{
  tasks: Task[]
  total: number
  loading: boolean
}>()

defineEmits<{
  'task-click': [task: Task]
  page: [event: { first: number; rows: number }]
}>()
</script>

<template>
  <DataTable
    :value="tasks"
    :total-records="total"
    :loading="loading"
    :rows="DEFAULT_PAGE_SIZE"
    paginator
    striped-rows
    row-hover
    class="text-sm"
    @row-click="$emit('task-click', $event.data)"
    @page="$emit('page', $event)"
  >
    <Column field="title" header="Title" sortable class="max-w-xs truncate" />
    <Column field="status" header="Status" sortable style="width: 120px">
      <template #body="{ data }">
        <StatusBadge :value="data.status" />
      </template>
    </Column>
    <Column field="priority" header="Priority" sortable style="width: 100px">
      <template #body="{ data }">
        <StatusBadge :value="data.priority" type="priority" />
      </template>
    </Column>
    <Column field="assigned_to" header="Assignee" style="width: 150px">
      <template #body="{ data }">
        <span class="text-slate-300">{{ data.assigned_to ?? '—' }}</span>
      </template>
    </Column>
    <Column field="type" header="Type" sortable style="width: 100px" />
    <Column field="updated_at" header="Updated" sortable style="width: 160px">
      <template #body="{ data }">
        <span class="text-slate-400">{{ formatDate(data.updated_at) }}</span>
      </template>
    </Column>
  </DataTable>
</template>
