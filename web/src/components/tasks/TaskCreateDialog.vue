<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import Dialog from 'primevue/dialog'
import InputText from 'primevue/inputtext'
import Textarea from 'primevue/textarea'
import Dropdown from 'primevue/dropdown'
import InputNumber from 'primevue/inputnumber'
import Button from 'primevue/button'
import type { CreateTaskRequest, TaskType, Priority, Complexity } from '@/api/types'

const props = defineProps<{
  visible: boolean
  agents: string[]
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  create: [data: CreateTaskRequest]
}>()

const title = ref('')
const description = ref('')
const type = ref<TaskType>('development')
const priority = ref<Priority>('medium')
const project = ref('')
const createdBy = ref('')
const assignedTo = ref<string | null>(null)
const complexity = ref<Complexity>('medium')
const budgetLimit = ref(0)

const typeOptions = [
  { label: 'Development', value: 'development' },
  { label: 'Design', value: 'design' },
  { label: 'Research', value: 'research' },
  { label: 'Review', value: 'review' },
  { label: 'Meeting', value: 'meeting' },
  { label: 'Admin', value: 'admin' },
]

const priorityOptions = [
  { label: 'Critical', value: 'critical' },
  { label: 'High', value: 'high' },
  { label: 'Medium', value: 'medium' },
  { label: 'Low', value: 'low' },
]

const complexityOptions = [
  { label: 'Simple', value: 'simple' },
  { label: 'Medium', value: 'medium' },
  { label: 'Complex', value: 'complex' },
  { label: 'Epic', value: 'epic' },
]

watch(
  () => props.visible,
  (v) => {
    if (v) resetForm()
  },
)

function resetForm() {
  title.value = ''
  description.value = ''
  type.value = 'development'
  priority.value = 'medium'
  project.value = ''
  createdBy.value = ''
  assignedTo.value = null
  complexity.value = 'medium'
  budgetLimit.value = 0
}

function handleSubmit() {
  const data: CreateTaskRequest = {
    title: title.value,
    description: description.value,
    type: type.value,
    priority: priority.value,
    project: project.value,
    created_by: createdBy.value,
    assigned_to: assignedTo.value,
    estimated_complexity: complexity.value,
    budget_limit: budgetLimit.value,
  }
  emit('create', data)
}

const isValid = computed(() => !!title.value.trim() && !!description.value.trim() && !!project.value.trim() && !!createdBy.value.trim())
</script>

<template>
  <Dialog
    :visible="visible"
    header="Create Task"
    modal
    class="w-[500px]"
    @update:visible="$emit('update:visible', $event)"
  >
    <form class="space-y-4" @submit.prevent="handleSubmit">
      <div>
        <label class="mb-1 block text-sm text-slate-300">Title</label>
        <InputText v-model="title" class="w-full" placeholder="Task title" />
      </div>
      <div>
        <label class="mb-1 block text-sm text-slate-300">Description</label>
        <Textarea v-model="description" class="w-full" rows="3" placeholder="Describe the task" />
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="mb-1 block text-sm text-slate-300">Type</label>
          <Dropdown v-model="type" :options="typeOptions" option-label="label" option-value="value" class="w-full" />
        </div>
        <div>
          <label class="mb-1 block text-sm text-slate-300">Priority</label>
          <Dropdown v-model="priority" :options="priorityOptions" option-label="label" option-value="value" class="w-full" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="mb-1 block text-sm text-slate-300">Project</label>
          <InputText v-model="project" class="w-full" placeholder="Project ID" />
        </div>
        <div>
          <label class="mb-1 block text-sm text-slate-300">Created By</label>
          <InputText v-model="createdBy" class="w-full" placeholder="Agent name" />
        </div>
      </div>
      <div class="grid grid-cols-2 gap-4">
        <div>
          <label class="mb-1 block text-sm text-slate-300">Assign To</label>
          <Dropdown
            v-model="assignedTo"
            :options="agents"
            placeholder="Select agent"
            show-clear
            class="w-full"
          />
        </div>
        <div>
          <label class="mb-1 block text-sm text-slate-300">Complexity</label>
          <Dropdown v-model="complexity" :options="complexityOptions" option-label="label" option-value="value" class="w-full" />
        </div>
      </div>
      <div>
        <label class="mb-1 block text-sm text-slate-300">Budget Limit (USD)</label>
        <InputNumber v-model="budgetLimit" mode="currency" currency="USD" :min="0" class="w-full" />
      </div>
    </form>

    <template #footer>
      <Button label="Cancel" text @click="$emit('update:visible', false)" />
      <Button label="Create" icon="pi pi-plus" :disabled="!isValid" @click="handleSubmit" />
    </template>
  </Dialog>
</template>
