<script setup lang="ts">
import { ref, watch } from 'vue'
import Sidebar from 'primevue/sidebar'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Dropdown from 'primevue/dropdown'
import StatusBadge from '@/components/common/StatusBadge.vue'
import type { Task, Priority, TaskStatus } from '@/api/types'
import { VALID_TRANSITIONS, TERMINAL_STATUSES } from '@/utils/constants'
import { formatDate, formatCurrency } from '@/utils/format'
import { useAuth } from '@/composables/useAuth'

const props = defineProps<{
  task: Task | null
  visible: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  save: [taskId: string, data: { title?: string; description?: string; priority?: Priority }]
  transition: [taskId: string, targetStatus: TaskStatus, version: number]
  cancel: [taskId: string, reason: string]
}>()

const { canWrite } = useAuth()
const editing = ref(false)
const editTitle = ref('')
const editDescription = ref('')
const editPriority = ref<Priority>('medium')
const cancelReason = ref('')
const showCancel = ref(false)

const priorityOptions = [
  { label: 'Critical', value: 'critical' },
  { label: 'High', value: 'high' },
  { label: 'Medium', value: 'medium' },
  { label: 'Low', value: 'low' },
]

watch(
  () => props.task,
  (task) => {
    if (task) {
      editTitle.value = task.title
      editDescription.value = task.description
      editPriority.value = task.priority
      editing.value = false
      showCancel.value = false
      cancelReason.value = ''
    }
  },
  { immediate: true },
)

function getTransitions(): readonly TaskStatus[] {
  if (!props.task) return []
  return VALID_TRANSITIONS[props.task.status] ?? []
}

function saveEdit() {
  if (!props.task) return
  emit('save', props.task.id, {
    title: editTitle.value,
    description: editDescription.value,
    priority: editPriority.value,
  })
  editing.value = false
}

function handleTransition(status: TaskStatus) {
  if (!props.task) return
  emit('transition', props.task.id, status, props.task.version ?? 0)
}

function handleCancel() {
  if (!props.task || !cancelReason.value.trim()) return
  emit('cancel', props.task.id, cancelReason.value)
  showCancel.value = false
}
</script>

<template>
  <Sidebar :visible="visible" position="right" class="w-[480px]" @update:visible="$emit('update:visible', $event)">
    <template #header>
      <span class="text-lg font-semibold text-slate-100">Task Details</span>
    </template>

    <div v-if="task" class="space-y-6">
      <!-- Title & Description -->
      <div v-if="!editing">
        <h2 class="text-lg font-medium text-slate-100">{{ task.title }}</h2>
        <p class="mt-2 text-sm text-slate-400 whitespace-pre-wrap">{{ task.description }}</p>
        <Button
          v-if="canWrite && !TERMINAL_STATUSES.has(task.status)"
          label="Edit"
          icon="pi pi-pencil"
          text
          size="small"
          class="mt-2"
          @click="editing = true"
        />
      </div>
      <div v-else class="space-y-3">
        <div>
          <label for="edit-title" class="mb-1 block text-xs text-slate-400">Title</label>
          <InputText id="edit-title" v-model="editTitle" class="w-full" />
        </div>
        <div>
          <label for="edit-description" class="mb-1 block text-xs text-slate-400">Description</label>
          <Textarea id="edit-description" v-model="editDescription" class="w-full" rows="4" />
        </div>
        <div>
          <label for="edit-priority" class="mb-1 block text-xs text-slate-400">Priority</label>
          <Dropdown input-id="edit-priority" v-model="editPriority" :options="priorityOptions" option-label="label" option-value="value" class="w-full" />
        </div>
        <div class="flex gap-2">
          <Button label="Save" icon="pi pi-check" size="small" @click="saveEdit" />
          <Button label="Cancel" icon="pi pi-times" text size="small" @click="editing = false" />
        </div>
      </div>

      <!-- Metadata -->
      <div class="grid grid-cols-2 gap-4 rounded-lg border border-slate-800 p-4">
        <div>
          <p class="text-xs text-slate-500">Status</p>
          <StatusBadge :value="task.status" />
        </div>
        <div>
          <p class="text-xs text-slate-500">Priority</p>
          <StatusBadge :value="task.priority" type="priority" />
        </div>
        <div>
          <p class="text-xs text-slate-500">Type</p>
          <p class="text-sm text-slate-300">{{ task.type }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Complexity</p>
          <p class="text-sm text-slate-300">{{ task.estimated_complexity }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Assignee</p>
          <p class="text-sm text-slate-300">{{ task.assigned_to ?? 'Unassigned' }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Project</p>
          <p class="text-sm text-slate-300">{{ task.project }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Budget Limit</p>
          <p class="text-sm text-slate-300">{{ formatCurrency(task.budget_limit) }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Cost</p>
          <p class="text-sm text-slate-300">{{ formatCurrency(task.cost_usd ?? 0) }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Created</p>
          <p class="text-sm text-slate-300">{{ formatDate(task.created_at) }}</p>
        </div>
        <div>
          <p class="text-xs text-slate-500">Updated</p>
          <p class="text-sm text-slate-300">{{ formatDate(task.updated_at) }}</p>
        </div>
      </div>

      <!-- Transitions -->
      <div v-if="canWrite && getTransitions().length > 0">
        <p class="mb-2 text-xs font-medium text-slate-400">Transition To</p>
        <div class="flex flex-wrap gap-2">
          <Button
            v-for="status in getTransitions()"
            :key="status"
            :label="status.replaceAll('_', ' ')"
            size="small"
            outlined
            @click="handleTransition(status)"
          />
        </div>
      </div>

      <!-- Cancel -->
      <div v-if="canWrite && !TERMINAL_STATUSES.has(task.status)">
        <Button
          v-if="!showCancel"
          label="Cancel Task"
          icon="pi pi-times"
          severity="danger"
          text
          size="small"
          @click="showCancel = true"
        />
        <div v-else class="space-y-2">
          <label for="cancel-reason" class="mb-1 block text-xs text-slate-400">Reason for cancellation</label>
          <Textarea id="cancel-reason" v-model="cancelReason" placeholder="Reason for cancellation" class="w-full" rows="2" aria-required="true" />
          <div class="flex gap-2">
            <Button label="Confirm Cancel" severity="danger" size="small" :disabled="!cancelReason.trim()" @click="handleCancel" />
            <Button label="Back" text size="small" @click="showCancel = false" />
          </div>
        </div>
      </div>
    </div>
  </Sidebar>
</template>
