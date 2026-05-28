import { useCallback, useRef, useState } from 'react'

import type { Complexity, Priority, TaskType } from '@/api/types/enums'
import type {
  CreateTaskRequest,
  TaskBoardSubmissionResponse,
} from '@/api/types/tasks'

export interface TaskCreateFormState {
  title: string
  description: string
  type: TaskType
  priority: Priority
  project: string
  created_by: string
  assigned_to: string
  estimated_complexity: Complexity
  budget_limit: string
}

const INITIAL_TASK_FORM: TaskCreateFormState = {
  title: '',
  description: '',
  type: 'development',
  priority: 'medium',
  project: '',
  created_by: '',
  assigned_to: '',
  estimated_complexity: 'medium',
  budget_limit: '',
}

export type TaskCreateFormErrors = Partial<Record<keyof TaskCreateFormState, string>>

export interface TaskCreateFormController {
  form: TaskCreateFormState
  errors: TaskCreateFormErrors
  submitting: boolean
  updateField: <K extends keyof TaskCreateFormState>(key: K, value: TaskCreateFormState[K]) => void
  applyTemplate: (defaults: Partial<TaskCreateFormState>) => void
  handleSubmit: () => Promise<void>
}

interface UseTaskCreateFormArgs {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (data: CreateTaskRequest) => Promise<TaskBoardSubmissionResponse | null>
}

export function useTaskCreateForm({
  open,
  onOpenChange,
  onCreate,
}: UseTaskCreateFormArgs): TaskCreateFormController {
  const [form, setForm] = useState<TaskCreateFormState>(INITIAL_TASK_FORM)
  const [errors, setErrors] = useState<TaskCreateFormErrors>({})
  const [submitting, setSubmitting] = useState(false)

  // Reset form state on close (render-phase check mirroring AgentCreateDialog
  // and PackSelectionDialog so reopening does not show stale input).
  const prevOpenRef = useRef(open)
  if (!open && prevOpenRef.current) {
    setForm(INITIAL_TASK_FORM)
    setErrors({})
  }
  prevOpenRef.current = open

  const updateField = useCallback(
    <K extends keyof TaskCreateFormState>(key: K, value: TaskCreateFormState[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }))
      setErrors((prev) => ({ ...prev, [key]: undefined }))
    },
    [],
  )

  const applyTemplate = useCallback((defaults: Partial<TaskCreateFormState>) => {
    setForm((prev) => ({ ...prev, ...defaults }))
  }, [])

  const handleSubmit = useCallback(async () => {
    const next = validateTaskForm(form)
    setErrors(next)
    if (Object.keys(next).length > 0) return
    setSubmitting(true)
    try {
      // Sentinel-return: store owns toast + log on failure. ``null`` keeps
      // the dialog open so the user doesn't lose their filled-in form.
      const submission = await onCreate(buildPayload(form))
      if (submission === null) return
      setForm(INITIAL_TASK_FORM)
      onOpenChange(false)
    } finally {
      setSubmitting(false)
    }
  }, [form, onCreate, onOpenChange])

  return { form, errors, submitting, updateField, applyTemplate, handleSubmit }
}

function validateTaskForm(form: TaskCreateFormState): TaskCreateFormErrors {
  const next: TaskCreateFormErrors = {}
  if (!form.title.trim()) next.title = 'Title is required'
  if (!form.description.trim()) next.description = 'Description is required'
  if (!form.project.trim()) next.project = 'Project is required'
  if (!form.created_by.trim()) next.created_by = 'Creator is required'
  if (form.budget_limit !== '') {
    const n = Number(form.budget_limit)
    if (!Number.isFinite(n) || n < 0) {
      next.budget_limit = 'Budget must be a non-negative number'
    }
  }
  return next
}

function buildPayload(form: TaskCreateFormState): CreateTaskRequest {
  return {
    title: form.title.trim(),
    description: form.description.trim(),
    type: form.type,
    priority: form.priority,
    project: form.project.trim(),
    created_by: form.created_by.trim(),
    assigned_to: form.assigned_to.trim() || undefined,
    estimated_complexity: form.estimated_complexity,
    budget_limit: form.budget_limit ? Number(form.budget_limit) : 0,
  }
}
