import { useCallback, useEffect, useRef, useState } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { Loader2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import type { Artifact, CreateArtifactRequest } from '@/api/types/artifacts'
import type { ArtifactType } from '@/api/types/enums'

const TYPE_OPTIONS: ReadonlyArray<{ value: ArtifactType; label: string }> = [
  { value: 'code', label: 'Code' },
  { value: 'tests', label: 'Tests' },
  { value: 'documentation', label: 'Documentation' },
]

export interface ArtifactCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (data: CreateArtifactRequest) => Promise<Artifact | null>
}

interface FormState {
  type: ArtifactType
  path: string
  task_id: string
  created_by: string
  description: string
  content_type: string
  project_id: string
}

const INITIAL_FORM: FormState = {
  type: 'code',
  path: '',
  task_id: '',
  created_by: '',
  description: '',
  content_type: '',
  project_id: '',
}

export function ArtifactCreateDialog({ open, onOpenChange, onCreate }: ArtifactCreateDialogProps) {
  const [form, setForm] = useState<FormState>(INITIAL_FORM)
  const [errors, setErrors] = useState<Partial<Record<keyof FormState, string>>>({})
  const [submitting, setSubmitting] = useState(false)

  // Reset on close runs in an effect rather than during render: calling
  // setForm/setErrors during render triggers an extra render and trips
  // Strict Mode invariants on the first opened-then-closed cycle.
  const prevOpenRef = useRef(open)
  useEffect(() => {
    if (!open && prevOpenRef.current) {
      setForm(INITIAL_FORM)
      setErrors({})
    }
    prevOpenRef.current = open
  }, [open])

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
    setErrors((prev) => ({ ...prev, [key]: undefined }))
  }

  const handleSubmit = useCallback(async () => {
    const next: Partial<Record<keyof FormState, string>> = {}
    if (!form.path.trim()) next.path = 'Path is required'
    if (!form.task_id.trim()) next.task_id = 'Task id is required'
    if (!form.created_by.trim()) next.created_by = 'Creator is required'
    setErrors(next)
    if (Object.keys(next).length > 0) return

    setSubmitting(true)
    try {
      const payload: CreateArtifactRequest = {
        type: form.type,
        path: form.path.trim(),
        task_id: form.task_id.trim(),
        created_by: form.created_by.trim(),
        ...(form.description.trim() ? { description: form.description.trim() } : {}),
        ...(form.content_type.trim() ? { content_type: form.content_type.trim() } : {}),
        ...(form.project_id.trim() ? { project_id: form.project_id.trim() } : {}),
      }
      const created = await onCreate(payload)
      if (created === null) return
      onOpenChange(false)
    } finally {
      setSubmitting(false)
    }
  }, [form, onCreate, onOpenChange])

  return (
    <Dialog.Root open={open} onOpenChange={(v: boolean) => { if (!submitting) onOpenChange(v) }}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-[var(--so-transition-default)] ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
        <Dialog.Popup
          className={cn(
            'fixed top-1/2 left-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border-bright bg-surface p-card-tight sm:p-card md:p-card-roomy shadow-[var(--so-shadow-card-hover)]',
            'transition-[opacity,translate,scale] duration-[var(--so-transition-default)] ease-out',
            'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
            'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
          )}
        >
          <div className="flex items-center justify-between mb-4">
            <Dialog.Title className="text-base font-semibold text-foreground">
              New Artifact
            </Dialog.Title>
            <Dialog.Close
              render={
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Close"
                  disabled={submitting}
                >
                  <X className="size-4" />
                </Button>
              }
            />
          </div>

          <div className="space-y-4">
            <SelectField
              label="Type"
              options={TYPE_OPTIONS}
              value={form.type}
              onChange={(value) => updateField('type', value as ArtifactType)}
            />

            <InputField
              label="Path"
              value={form.path}
              onChange={(e) => updateField('path', e.target.value)}
              error={errors.path}
              required
              autoFocus
              placeholder="src/example/file.py"
            />

            <InputField
              label="Task id"
              value={form.task_id}
              onChange={(e) => updateField('task_id', e.target.value)}
              error={errors.task_id}
              required
              placeholder="Originating task id"
            />

            <InputField
              label="Created by"
              value={form.created_by}
              onChange={(e) => updateField('created_by', e.target.value)}
              error={errors.created_by}
              required
              placeholder="Agent or user name"
            />

            <InputField
              label="Description"
              value={form.description}
              onChange={(e) => updateField('description', e.target.value)}
              hint="Optional summary"
            />

            <InputField
              label="Content type"
              value={form.content_type}
              onChange={(e) => updateField('content_type', e.target.value)}
              hint="Optional MIME type, e.g. text/plain"
            />

            <InputField
              label="Project id"
              value={form.project_id}
              onChange={(e) => updateField('project_id', e.target.value)}
              hint="Optional project association"
            />

            <div className="flex justify-end gap-3 pt-2">
              <Dialog.Close
                render={
                  <Button variant="outline" disabled={submitting}>Cancel</Button>
                }
              />
              <Button disabled={submitting} onClick={handleSubmit}>
                {submitting && <Loader2 className="mr-2 size-4 animate-spin" />}
                Create artifact
              </Button>
            </div>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
