import { useState } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { Loader2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { importCodebase } from '@/api/endpoints/brownfield'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'

export interface BrownfieldImportDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  projectId: string
}

const POPUP_CLASS = cn(
  'fixed top-1/2 left-1/2 z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 -translate-y-1/2',
  'rounded-xl border border-border-bright bg-surface p-card-tight sm:p-card md:p-card-roomy shadow-[var(--so-shadow-card-hover)]',
  'transition-[opacity,translate,scale] duration-[var(--so-transition-default)] ease-out',
  'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
  'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
)

interface ImportFormState {
  sourceRef: string
  title: string
  branch: string
}

const EMPTY_FORM: ImportFormState = { sourceRef: '', title: '', branch: 'main' }

function ImportForm({
  form,
  setForm,
  submitting,
  onSubmit,
}: {
  form: ImportFormState
  setForm: (next: ImportFormState) => void
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
        label="Source"
        value={form.sourceRef}
        onValueChange={(v) => setForm({ ...form, sourceRef: v })}
        required
        autoFocus
        placeholder="https://github.com/org/repo.git or /path/to/repo"
        hint="Remote clone URL or local path to import from"
      />
      <InputField
        label="Title"
        value={form.title}
        onValueChange={(v) => setForm({ ...form, title: v })}
        placeholder="Imported codebase"
        hint="Optional label for the indexed knowledge source"
      />
      <InputField
        label="Default branch"
        value={form.branch}
        onValueChange={(v) => setForm({ ...form, branch: v })}
        placeholder="main"
      />
      <div className="flex justify-end gap-3 pt-2">
        <Dialog.Close
          render={
            <Button variant="outline" type="button" disabled={submitting}>
              Cancel
            </Button>
          }
        />
        <Button type="submit" disabled={submitting || form.sourceRef.trim() === ''}>
          {submitting && <Loader2 className="mr-2 size-4 animate-spin" />}
          Start import
        </Button>
      </div>
    </form>
  )
}

/**
 * Kick off a brownfield codebase import into the current project. The
 * import + analysis run asynchronously; on success the dialog closes and
 * the operator watches the project's structure map / tasks fill in.
 */
export function BrownfieldImportDialog({ open, onOpenChange, projectId }: BrownfieldImportDialogProps) {
  const [form, setForm] = useState<ImportFormState>(EMPTY_FORM)
  const [submitting, setSubmitting] = useState(false)

  const submit = async () => {
    if (form.sourceRef.trim() === '' || submitting) return
    setSubmitting(true)
    try {
      await importCodebase({
        project_id: projectId,
        source_ref: form.sourceRef.trim(),
        ...(form.title.trim() ? { title: form.title.trim() } : {}),
        ...(form.branch.trim() ? { default_branch: form.branch.trim() } : {}),
      })
      useToastStore.getState().add({
        variant: 'success',
        title: 'Import started',
        description: 'The codebase is importing in the background. Tasks appear as it completes.',
      })
      setForm(EMPTY_FORM)
      onOpenChange(false)
    } catch (err) {
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Could not start import'),
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
              Import codebase
            </Dialog.Title>
            <Dialog.Close
              render={
                <Button variant="ghost" size="icon" aria-label="Close" disabled={submitting}>
                  <X className="size-4" />
                </Button>
              }
            />
          </div>
          <ImportForm form={form} setForm={setForm} submitting={submitting} onSubmit={() => void submit()} />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
