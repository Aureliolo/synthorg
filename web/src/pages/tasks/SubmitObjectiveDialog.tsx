import { useState } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { Loader2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { submitObjective } from '@/api/endpoints/objectives'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

export interface SubmitObjectiveDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Called with the server-minted submission id once the objective is accepted. */
  onSubmitted: (submissionId: string) => void
}

const POPUP_CLASS = cn(
  'fixed top-1/2 left-1/2 z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2',
  'rounded-xl border border-border-bright bg-surface p-card-tight sm:p-card md:p-card-roomy shadow-[var(--so-shadow-card-hover)]',
  'transition-[opacity,translate,scale] duration-[var(--so-transition-default)] ease-out',
  'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
  'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
)

interface ObjectiveFormState {
  title: string
  description: string
  requestedBy: string
}

const EMPTY_FORM: ObjectiveFormState = { title: '', description: '', requestedBy: '' }

function isValid(form: ObjectiveFormState): boolean {
  return form.title.trim() !== '' && form.description.trim() !== '' && form.requestedBy.trim() !== ''
}

function ObjectiveForm({
  form,
  setForm,
  submitting,
  onSubmit,
}: {
  form: ObjectiveFormState
  setForm: (next: ObjectiveFormState) => void
  submitting: boolean
  onSubmit: () => void
}) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit()
      }}
      className="space-y-4"
    >
      <InputField
        label="Title"
        value={form.title}
        onValueChange={(v) => setForm({ ...form, title: v })}
        required
        autoFocus
        placeholder="Short objective title"
      />
      <InputField
        label="Description"
        value={form.description}
        onValueChange={(v) => setForm({ ...form, description: v })}
        multiline
        rows={4}
        required
        placeholder="Detailed statement of the objective"
      />
      <InputField
        label="Requested by"
        value={form.requestedBy}
        onValueChange={(v) => setForm({ ...form, requestedBy: v })}
        required
        placeholder="Your name or service id"
      />
      <div className="flex justify-end gap-3 pt-2">
        <Dialog.Close
          render={
            <Button variant="outline" type="button" disabled={submitting}>
              Cancel
            </Button>
          }
        />
        <Button type="submit" disabled={submitting || !isValid(form)}>
          {submitting && <Loader2 className="mr-2 size-4 animate-spin" />}
          Submit
        </Button>
      </div>
    </form>
  )
}

/**
 * Submit a free-form objective for decomposition into tasks. The pipeline
 * run is asynchronous; on success the caller is handed the submission id so
 * it can correlate the resulting work on the task board.
 */
export function SubmitObjectiveDialog({ open, onOpenChange, onSubmitted }: SubmitObjectiveDialogProps) {
  const [form, setForm] = useState<ObjectiveFormState>(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)

  const submit = async () => {
    if (!isValid(form) || submitting) return
    setSubmitting(true)
    try {
      const ack = await submitObjective({
        title: form.title.trim(),
        description: form.description.trim(),
        requested_by: form.requestedBy.trim(),
      })
      useToastStore.getState().add({
        variant: 'success',
        title: 'Objective submitted',
        description: `Decomposition is running. Submission ${ack.submission_id.slice(0, 8)}.`,
      })
      setForm(EMPTY_FORM)
      onOpenChange(false)
      onSubmitted(ack.submission_id)
    } catch (err) {
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Could not submit objective'),
        description: getErrorMessage(err),
      })
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog.Root open={open} onOpenChange={(next: boolean) => { if (!submitting) onOpenChange(next) }}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-[var(--so-transition-default)] ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
        <Dialog.Popup className={POPUP_CLASS}>
          <div className="mb-4 flex items-center justify-between">
            <Dialog.Title className="text-base font-semibold text-foreground">
              Submit objective
            </Dialog.Title>
            <Dialog.Close
              render={
                <Button variant="ghost" size="icon" aria-label="Close" disabled={submitting}>
                  <X className="size-4" />
                </Button>
              }
            />
          </div>
          <ObjectiveForm form={form} setForm={setForm} submitting={submitting} onSubmit={() => void submit()} />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
