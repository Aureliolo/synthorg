import { Dialog } from '@base-ui/react/dialog'
import { Loader2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import type { Artifact, CreateArtifactRequest } from '@/api/types/artifacts'
import type { ArtifactType } from '@/api/types/enums'
import { makeEnumParser } from '@/utils/type-guards'

import {
  useArtifactCreateForm,
  type ArtifactCreateFormController,
  type ArtifactFormState,
  type ArtifactFormErrors,
} from './useArtifactCreateForm'

const TYPE_OPTIONS: ReadonlyArray<{ value: ArtifactType; label: string }> = [
  { value: 'code', label: 'Code' },
  { value: 'tests', label: 'Tests' },
  { value: 'documentation', label: 'Documentation' },
]

const parseArtifactType = makeEnumParser<ArtifactType>(TYPE_OPTIONS.map((o) => o.value))

export interface ArtifactCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (data: CreateArtifactRequest) => Promise<Artifact | null>
}

export function ArtifactCreateDialog({
  open,
  onOpenChange,
  onCreate,
}: ArtifactCreateDialogProps) {
  const ctrl = useArtifactCreateForm({ open, onOpenChange, onCreate })

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next: boolean) => {
        if (!ctrl.submitting) onOpenChange(next)
      }}
    >
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
          <ArtifactCreateDialogHeader submitting={ctrl.submitting} />
          <ArtifactCreateForm ctrl={ctrl} />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

interface ArtifactCreateDialogHeaderProps {
  submitting: boolean
}

function ArtifactCreateDialogHeader({ submitting }: ArtifactCreateDialogHeaderProps) {
  return (
    <div className="flex items-center justify-between mb-4">
      <Dialog.Title className="text-base font-semibold text-foreground">
        New Artifact
      </Dialog.Title>
      <Dialog.Close
        render={
          <Button variant="ghost" size="icon" aria-label="Close" disabled={submitting}>
            <X className="size-4" />
          </Button>
        }
      />
    </div>
  )
}

interface ArtifactCreateFormProps {
  ctrl: ArtifactCreateFormController
}

function ArtifactCreateForm({ ctrl }: ArtifactCreateFormProps) {
  return (
    <form onSubmit={ctrl.handleSubmit} className="space-y-4">
      <ArtifactFormFields
        form={ctrl.form}
        errors={ctrl.errors}
        updateField={ctrl.updateField}
      />
      <div className="flex justify-end gap-3 pt-2">
        <Dialog.Close
          render={
            <Button variant="outline" disabled={ctrl.submitting} type="button">
              Cancel
            </Button>
          }
        />
        <Button type="submit" disabled={ctrl.submitting}>
          {ctrl.submitting && <Loader2 className="mr-2 size-4 animate-spin" />}
          Create artifact
        </Button>
      </div>
    </form>
  )
}

interface ArtifactFormFieldsProps {
  form: ArtifactFormState
  errors: ArtifactFormErrors
  updateField: ArtifactCreateFormController['updateField']
}

function ArtifactFormFields({ form, errors, updateField }: ArtifactFormFieldsProps) {
  return (
    <>
      <SelectField
        label="Type"
        options={TYPE_OPTIONS}
        value={form.type}
        onChange={(value) => {
          const type = parseArtifactType(value)
          if (type) updateField('type', type)
        }}
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
    </>
  )
}
