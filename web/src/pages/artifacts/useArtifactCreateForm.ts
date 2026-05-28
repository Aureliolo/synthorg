import { useCallback, useEffect, useRef, useState } from 'react'

import type { Artifact, CreateArtifactRequest } from '@/api/types/artifacts'
import type { ArtifactType } from '@/api/types/enums'

export interface ArtifactFormState {
  type: ArtifactType
  path: string
  task_id: string
  created_by: string
  description: string
  content_type: string
  project_id: string
}

const INITIAL_ARTIFACT_FORM: ArtifactFormState = {
  type: 'code',
  path: '',
  task_id: '',
  created_by: '',
  description: '',
  content_type: '',
  project_id: '',
}

export type ArtifactFormErrors = Partial<Record<keyof ArtifactFormState, string>>

export interface ArtifactCreateFormController {
  form: ArtifactFormState
  errors: ArtifactFormErrors
  submitting: boolean
  updateField: <K extends keyof ArtifactFormState>(key: K, value: ArtifactFormState[K]) => void
  handleSubmit: (e?: React.FormEvent) => Promise<void>
}

interface UseArtifactCreateFormArgs {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (data: CreateArtifactRequest) => Promise<Artifact | null>
}

export function useArtifactCreateForm({
  open,
  onOpenChange,
  onCreate,
}: UseArtifactCreateFormArgs): ArtifactCreateFormController {
  const [form, setForm] = useState<ArtifactFormState>(INITIAL_ARTIFACT_FORM)
  const [errors, setErrors] = useState<ArtifactFormErrors>({})
  const [submitting, setSubmitting] = useState(false)

  // Reset on close runs in an effect rather than during render: calling
  // setForm/setErrors during render triggers an extra render and trips
  // Strict Mode invariants on the first opened-then-closed cycle.
  const prevOpenRef = useRef(open)
  useEffect(() => {
    if (!open && prevOpenRef.current) {
      setForm(INITIAL_ARTIFACT_FORM)
      setErrors({})
    }
    prevOpenRef.current = open
  }, [open])

  const updateField = useCallback(
    <K extends keyof ArtifactFormState>(key: K, value: ArtifactFormState[K]) => {
      setForm((prev) => ({ ...prev, [key]: value }))
      setErrors((prev) => ({ ...prev, [key]: undefined }))
    },
    [],
  )

  // Synchronous in-flight guard: React batches setState, so a second click that
  // arrives before the next render still sees ``submitting === false``. The ref
  // flips inside the same call frame, so the second invocation early-returns.
  const isSubmittingRef = useRef(false)

  const handleSubmit = useCallback(
    async (e?: React.FormEvent) => {
      e?.preventDefault()
      if (isSubmittingRef.current) return
      const nextErrors = validateArtifactForm(form)
      setErrors(nextErrors)
      if (Object.keys(nextErrors).length > 0) return
      isSubmittingRef.current = true
      setSubmitting(true)
      try {
        const created = await onCreate(buildCreatePayload(form))
        if (created === null) return
        onOpenChange(false)
      } finally {
        isSubmittingRef.current = false
        setSubmitting(false)
      }
    },
    [form, onCreate, onOpenChange],
  )

  return { form, errors, submitting, updateField, handleSubmit }
}

function validateArtifactForm(form: ArtifactFormState): ArtifactFormErrors {
  const next: ArtifactFormErrors = {}
  if (!form.path.trim()) next.path = 'Path is required'
  if (!form.task_id.trim()) next.task_id = 'Task id is required'
  if (!form.created_by.trim()) next.created_by = 'Creator is required'
  return next
}

function buildCreatePayload(form: ArtifactFormState): CreateArtifactRequest {
  return {
    type: form.type,
    path: form.path.trim(),
    task_id: form.task_id.trim(),
    created_by: form.created_by.trim(),
    description: form.description.trim(),
    content_type: form.content_type.trim(),
    ...(form.project_id.trim() ? { project_id: form.project_id.trim() } : {}),
  }
}
