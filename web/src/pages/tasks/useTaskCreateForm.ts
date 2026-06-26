import { useCallback, useRef, useState } from 'react'

import type { Complexity, Priority, TaskType } from '@/api/types/enums'
import type {
  CreateTaskRequest,
  TaskBoardSubmissionResponse,
} from '@/api/types/tasks'

// The board files into the work pipeline, which owns provenance (from the
// authenticated requester), assignment (routing's capability-matched
// selection), and the budget ceiling (the approved cost forecast). The form
// therefore collects only the fields the filing actually carries.
export interface TaskCreateFormState {
  title: string
  description: string
  type: TaskType
  priority: Priority
  project: string
  estimated_complexity: Complexity
}

const INITIAL_TASK_FORM: TaskCreateFormState = {
  title: '',
  description: '',
  type: 'development',
  priority: 'medium',
  project: '',
  estimated_complexity: 'medium',
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
  return next
}

function buildPayload(form: TaskCreateFormState): CreateTaskRequest {
  return {
    title: form.title.trim(),
    description: form.description.trim(),
    type: form.type,
    priority: form.priority,
    project: form.project.trim(),
    estimated_complexity: form.estimated_complexity,
  }
}
