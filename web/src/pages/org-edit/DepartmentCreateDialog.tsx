import { useCallback, useRef, useState } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { Loader2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { getErrorMessage } from '@/utils/errors'
import type { CreateDepartmentRequest, Department } from '@/api/types/org'

export interface DepartmentCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreate: (data: CreateDepartmentRequest) => Promise<Department>
}

interface FormState {
  name: string
  budget_percent: string
}

const INITIAL_FORM: FormState = {
  name: '',
  budget_percent: '0',
}

export function DepartmentCreateDialog({ open, onOpenChange, onCreate }: DepartmentCreateDialogProps) {
  const [form, setForm] = useState<FormState>(INITIAL_FORM)
  const [errors, setErrors] = useState<Partial<Record<keyof FormState, string>>>({})
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const prevOpenRef = useRef(open)
  if (!open && prevOpenRef.current) {
    setForm(INITIAL_FORM)
    setErrors({})
    setSubmitError(null)
  }
  prevOpenRef.current = open

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
    setErrors((prev) => ({ ...prev, [key]: undefined }))
    setSubmitError(null)
  }

  const handleSubmit = useCallback(async () => {
    const next: Partial<Record<keyof FormState, string>> = {}
    if (!form.name.trim()) next.name = 'Name is required'
    const pct = Number(form.budget_percent)
    if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
      next.budget_percent = 'Budget percent must be between 0 and 100'
    }
    setErrors(next)
    if (Object.keys(next).length > 0) return

    setSubmitting(true)
    setSubmitError(null)
    try {
      await onCreate({
        name: form.name.trim(),
        budget_percent: pct,
      })
      setForm(INITIAL_FORM)
      onOpenChange(false)
    } catch (err) {
      setSubmitError(getErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }, [form, onCreate, onOpenChange])

  return (
    <Dialog.Root open={open} onOpenChange={(v: boolean) => { if (!submitting) onOpenChange(v) }}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-200 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
        <Dialog.Popup
          className={cn(
            'fixed top-1/2 left-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border-bright bg-surface p-card-tight sm:p-card md:p-card-roomy shadow-[var(--so-shadow-card-hover)]',
            'transition-[opacity,translate,scale] duration-200 ease-out',
            'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
            'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
          )}
        >
          <div className="flex items-center justify-between mb-4">
            <Dialog.Title className="text-base font-semibold text-foreground">
              New Department
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
            <InputField
              label="Name"
              value={form.name}
              onChange={(e) => updateField('name', e.target.value)}
              error={errors.name}
              required
              autoFocus
              placeholder="e.g. engineering"
            />

            <InputField
              label="Budget %"
              type="number"
              value={form.budget_percent}
              onChange={(e) => updateField('budget_percent', e.target.value)}
              error={errors.budget_percent}
              hint="Percentage of company budget (0-100)"
            />

            {submitError && (
              <p className="text-xs text-danger">{submitError}</p>
            )}

            <div className="flex justify-end gap-3 pt-2">
              <Dialog.Close
                render={
                  <Button variant="outline" disabled={submitting}>Cancel</Button>
                }
              />
              <Button
                disabled={submitting}
                onClick={handleSubmit}
              >
                {submitting && <Loader2 className="mr-2 size-4 animate-spin" />}
                Create Department
              </Button>
            </div>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
