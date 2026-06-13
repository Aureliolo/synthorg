import { useCallback, useState } from 'react'
import { useWorkflowsStore } from '@/stores/workflows'
import { WORKFLOW_TYPES } from '@/utils/constants'

export type WorkflowCreateMode = 'blank' | 'blueprint'

interface WorkflowCreateFormState {
  name: string
  description: string
  workflowType: string
}

const INITIAL_FORM: WorkflowCreateFormState = {
  name: '',
  description: '',
  workflowType: 'sequential_pipeline',
}

type WorkflowCreateFormErrors = Partial<Record<keyof WorkflowCreateFormState, string>>

type WorkflowType = (typeof WORKFLOW_TYPES)[number]

function isWorkflowType(value: string): value is WorkflowType {
  return (WORKFLOW_TYPES as readonly string[]).includes(value)
}

export interface WorkflowCreateFormController {
  mode: WorkflowCreateMode
  selectedBlueprint: string | null
  form: WorkflowCreateFormState
  errors: WorkflowCreateFormErrors
  blueprintError: string | null
  submitting: boolean
  handleModeChange: (mode: WorkflowCreateMode) => void
  updateField: <K extends keyof WorkflowCreateFormState>(
    key: K,
    value: WorkflowCreateFormState[K],
  ) => void
  handleBlueprintSelect: (name: string | null) => void
  handleSubmit: () => Promise<void>
  handleClose: () => void
}

export function useWorkflowCreateForm(onClose: () => void): WorkflowCreateFormController {
  const [mode, setMode] = useState<WorkflowCreateMode>('blank')
  const [selectedBlueprint, setSelectedBlueprint] = useState<string | null>(null)
  const [form, setForm] = useState<WorkflowCreateFormState>(INITIAL_FORM)
  const [errors, setErrors] = useState<WorkflowCreateFormErrors>({})
  const [blueprintError, setBlueprintError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const blueprints = useWorkflowsStore((s) => s.blueprints)

  const updateField = useCallback(
    <K extends keyof WorkflowCreateFormState>(key: K, value: WorkflowCreateFormState[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }))
      setErrors((prev) => ({ ...prev, [key]: undefined }))
    },
    [],
  )

  const handleModeChange = useCallback(
    (next: WorkflowCreateMode) => {
      setMode(next)
      if (next !== 'blueprint') setBlueprintError(null)
    },
    [],
  )

  const handleBlueprintSelect = useCallback(
    (name: string | null) => {
      setSelectedBlueprint(name)
      setBlueprintError(null)
      if (!name) return
      const bp = blueprints.find((b) => b.name === name)
      if (!bp) return
      setForm((prev) => ({
        ...prev,
        name: bp.display_name,
        description: bp.description,
        workflowType: bp.workflow_type,
      }))
    },
    [blueprints],
  )

  const handleClose = useCallback(() => {
    setMode('blank')
    setSelectedBlueprint(null)
    setForm(INITIAL_FORM)
    setErrors({})
    setBlueprintError(null)
    onClose()
  }, [onClose])

  const handleSubmit = useCallback(async () => {
    if (mode === 'blueprint' && !selectedBlueprint) {
      setBlueprintError('Select a template or switch to Blank mode')
      return
    }
    const next = validateForm(form)
    setErrors(next)
    if (Object.keys(next).length > 0) return
    if (!isWorkflowType(form.workflowType)) return
    setSubmitting(true)
    const created = await submitWorkflow(mode, form, selectedBlueprint)
    setSubmitting(false)
    if (created) handleClose()
  }, [mode, selectedBlueprint, form, handleClose])

  return {
    mode,
    selectedBlueprint,
    form,
    errors,
    blueprintError,
    submitting,
    handleModeChange,
    updateField,
    handleBlueprintSelect,
    handleSubmit,
    handleClose,
  }
}

function validateForm(form: WorkflowCreateFormState): WorkflowCreateFormErrors {
  const next: WorkflowCreateFormErrors = {}
  if (!form.name.trim()) next.name = 'Name is required'
  if (!isWorkflowType(form.workflowType)) {
    next.workflowType = 'Select a valid workflow type'
  }
  return next
}

async function submitWorkflow(
  mode: WorkflowCreateMode,
  form: WorkflowCreateFormState,
  selectedBlueprint: string | null,
) {
  if (mode === 'blueprint') {
    return useWorkflowsStore.getState().createFromBlueprint({
      blueprint_name: selectedBlueprint!,
      name: form.name.trim(),
      ...(form.description.trim() ? { description: form.description.trim() } : {}),
    })
  }
  // `isWorkflowType` was checked by the caller before we reached the store call.
  if (!isWorkflowType(form.workflowType)) return null
  return useWorkflowsStore.getState().createWorkflow({
    name: form.name.trim(),
    description: form.description.trim(),
    version: '1.0.0',
    workflow_type: form.workflowType,
    inputs: [],
    outputs: [],
    is_subworkflow: false,
    nodes: [],
    edges: [],
  })
}
