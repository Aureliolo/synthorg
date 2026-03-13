<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import Button from 'primevue/button'
import { useToast } from 'primevue/usetoast'
import AppShell from '@/components/layout/AppShell.vue'
import PageHeader from '@/components/common/PageHeader.vue'
import LoadingSkeleton from '@/components/common/LoadingSkeleton.vue'
import ErrorBoundary from '@/components/common/ErrorBoundary.vue'
import KanbanBoard from '@/components/tasks/KanbanBoard.vue'
import TaskListView from '@/components/tasks/TaskListView.vue'
import TaskDetailPanel from '@/components/tasks/TaskDetailPanel.vue'
import TaskCreateDialog from '@/components/tasks/TaskCreateDialog.vue'
import TaskFilters from '@/components/tasks/TaskFilters.vue'
import { useTaskStore } from '@/stores/tasks'
import { useAgentStore } from '@/stores/agents'
import { useWebSocketStore } from '@/stores/websocket'
import { useAuthStore } from '@/stores/auth'
import { useAuth } from '@/composables/useAuth'
import { sanitizeForLog } from '@/utils/logging'
import type { Task, TaskStatus, Priority, CreateTaskRequest, TaskFilters as TaskFilterType } from '@/api/types'

const toast = useToast()
const taskStore = useTaskStore()
const agentStore = useAgentStore()
const wsStore = useWebSocketStore()
const authStore = useAuthStore()
const { canWrite } = useAuth()

const viewMode = ref<'kanban' | 'list'>('kanban')
const selectedTask = ref<Task | null>(null)
const detailVisible = ref(false)
const createVisible = ref(false)
const filters = ref<TaskFilterType>({})

const agentNames = computed(() => agentStore.agents.map((a) => a.name))

onMounted(async () => {
  try {
    if (authStore.token && !wsStore.connected) {
      wsStore.connect(authStore.token)
    }
    wsStore.subscribe(['tasks'])
    wsStore.onChannelEvent('tasks', taskStore.handleWsEvent)
  } catch (err) {
    console.error('WebSocket setup failed:', sanitizeForLog(err))
  }

  try {
    await Promise.all([
      taskStore.fetchTasks({ limit: 200 }),
      agentStore.fetchAgents(),
    ])
  } catch (err) {
    console.error('Initial data fetch failed:', sanitizeForLog(err))
  }
})

onUnmounted(() => {
  wsStore.unsubscribe(['tasks'])
  wsStore.offChannelEvent('tasks', taskStore.handleWsEvent)
})

function openDetail(task: Task) {
  selectedTask.value = task
  detailVisible.value = true
}

async function handleTransition(taskId: string, targetStatus: TaskStatus, version: number) {
  try {
    const result = await taskStore.transitionTask(taskId, {
      target_status: targetStatus,
      expected_version: version,
    })
    if (result) {
      selectedTask.value = result
      toast.add({ severity: 'success', summary: 'Task transitioned', life: 3000 })
    } else {
      toast.add({ severity: 'error', summary: taskStore.error ?? 'Transition failed', life: 5000 })
    }
  } catch (err) {
    console.error('Task transition failed:', sanitizeForLog(err))
    toast.add({ severity: 'error', summary: 'Transition failed', life: 5000 })
  }
}

async function handleSave(taskId: string, data: { title?: string; description?: string; priority?: Priority }) {
  try {
    const result = await taskStore.updateTask(taskId, data)
    if (result) {
      selectedTask.value = result
      toast.add({ severity: 'success', summary: 'Task updated', life: 3000 })
    } else {
      toast.add({ severity: 'error', summary: taskStore.error ?? 'Update failed', life: 5000 })
    }
  } catch (err) {
    console.error('Task update failed:', sanitizeForLog(err))
    toast.add({ severity: 'error', summary: 'Update failed', life: 5000 })
  }
}

async function handleCancel(taskId: string, reason: string) {
  try {
    const result = await taskStore.cancelTask(taskId, { reason })
    if (result) {
      selectedTask.value = result
      toast.add({ severity: 'info', summary: 'Task cancelled', life: 3000 })
    } else {
      toast.add({ severity: 'error', summary: taskStore.error ?? 'Cancel failed', life: 5000 })
    }
  } catch (err) {
    console.error('Task cancel failed:', sanitizeForLog(err))
    toast.add({ severity: 'error', summary: 'Cancel failed', life: 5000 })
  }
}

async function handleCreate(data: CreateTaskRequest) {
  try {
    const result = await taskStore.createTask(data)
    if (result) {
      createVisible.value = false
      toast.add({ severity: 'success', summary: 'Task created', life: 3000 })
    } else {
      toast.add({ severity: 'error', summary: taskStore.error ?? 'Create failed', life: 5000 })
    }
  } catch (err) {
    console.error('Task create failed:', sanitizeForLog(err))
    toast.add({ severity: 'error', summary: 'Create failed', life: 5000 })
  }
}

async function handleTaskMoved(task: Task, targetStatus: TaskStatus) {
  await handleTransition(task.id, targetStatus, task.version ?? 0)
}

async function handleFilterUpdate(newFilters: TaskFilterType) {
  filters.value = { ...filters.value, ...newFilters }
  try {
    await taskStore.fetchTasks(filters.value)
  } catch (err) {
    console.error('Filter fetch failed:', sanitizeForLog(err))
  }
}

async function handleFilterReset() {
  filters.value = {}
  try {
    await taskStore.fetchTasks({})
  } catch (err) {
    console.error('Filter reset fetch failed:', sanitizeForLog(err))
  }
}
</script>

<template>
  <AppShell>
    <PageHeader title="Tasks" subtitle="Manage and track tasks across your organization">
      <template #actions>
        <div class="flex items-center gap-3">
          <TaskFilters
            :agents="agentNames"
            :filters="filters"
            @update="handleFilterUpdate"
            @reset="handleFilterReset"
          />
          <div class="flex rounded-lg border border-slate-700" role="group" aria-label="View mode">
            <button
              :class="['px-3 py-1.5 text-xs', viewMode === 'kanban' ? 'bg-brand-600 text-white' : 'text-slate-400']"
              :aria-pressed="viewMode === 'kanban'"
              @click="viewMode = 'kanban'"
            >
              Board
            </button>
            <button
              :class="['px-3 py-1.5 text-xs', viewMode === 'list' ? 'bg-brand-600 text-white' : 'text-slate-400']"
              :aria-pressed="viewMode === 'list'"
              @click="viewMode = 'list'"
            >
              List
            </button>
          </div>
          <Button
            v-if="canWrite"
            label="New Task"
            icon="pi pi-plus"
            size="small"
            @click="createVisible = true"
          />
        </div>
      </template>
    </PageHeader>

    <ErrorBoundary :error="taskStore.error ?? agentStore.error" @retry="() => { taskStore.fetchTasks(filters); agentStore.fetchAgents() }">
      <LoadingSkeleton v-if="taskStore.loading && taskStore.tasks.length === 0" :lines="8" />
      <template v-else>
        <KanbanBoard
          v-if="viewMode === 'kanban'"
          :tasks-by-status="taskStore.tasksByStatus"
          @task-click="openDetail"
          @task-moved="handleTaskMoved"
        />
        <TaskListView
          v-else
          :tasks="taskStore.tasks"
          :total="taskStore.total"
          :loading="taskStore.loading"
          @task-click="openDetail"
        />
      </template>
    </ErrorBoundary>

    <TaskDetailPanel
      :task="selectedTask"
      :visible="detailVisible"
      @update:visible="detailVisible = $event"
      @save="handleSave"
      @transition="handleTransition"
      @cancel="handleCancel"
    />

    <TaskCreateDialog
      :visible="createVisible"
      :agents="agentNames"
      @update:visible="createVisible = $event"
      @create="handleCreate"
    />
  </AppShell>
</template>
