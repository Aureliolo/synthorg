import { useCallback, useMemo, useRef, useState } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { Loader2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { InputField } from '@/components/ui/input-field'
import { SelectField } from '@/components/ui/select-field'
import type { AgentConfig } from '@/api/types/agents'
import { SENIORITY_LEVEL_VALUES, type SeniorityLevel } from '@/api/types/enums'
import type { CreateAgentOrgRequest, Department } from '@/api/types/org'
import { makeEnumParser } from '@/utils/type-guards'

export interface AgentCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  departments: readonly Department[]
  onCreate: (data: CreateAgentOrgRequest) => Promise<AgentConfig | null>
}

interface FormState {
  name: string
  role: string
  department: string
  level: SeniorityLevel
}

type FormErrors = Partial<Record<keyof FormState, string>>
type UpdateFieldFn = <K extends keyof FormState>(key: K, value: FormState[K]) => void

const INITIAL_FORM: FormState = { name: '', role: '', department: '', level: 'mid' }
const LEVEL_OPTIONS = SENIORITY_LEVEL_VALUES.map((l) => ({ value: l, label: l }))
const parseSeniorityLevel = makeEnumParser<SeniorityLevel>(SENIORITY_LEVEL_VALUES)

function validateAgentForm(form: FormState): FormErrors {
  const next: FormErrors = {}
  if (!form.name.trim()) next.name = 'Name is required'
  if (!form.role.trim()) next.role = 'Role is required'
  if (!form.department) next.department = 'Department is required'
  return next
}

const POPUP_CLASS = cn(
  'fixed top-1/2 left-1/2 z-50 w-full max-w-md -translate-x-1/2 -translate-y-1/2',
  'rounded-xl border border-border-bright bg-surface p-card-tight sm:p-card md:p-card-roomy shadow-[var(--so-shadow-card-hover)]',
  'transition-[opacity,translate,scale] duration-200 ease-out',
  'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
  'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
)

interface AgentCreateBodyProps {
  form: FormState
  errors: FormErrors
  updateField: UpdateFieldFn
  deptOptions: { value: string; label: string }[]
  submitting: boolean
  onSubmit: () => void
}

function AgentCreateBody({ form, errors, updateField, deptOptions, submitting, onSubmit }: AgentCreateBodyProps) {
  return (
    <>
      <div className="flex items-center justify-between mb-4">
        <Dialog.Title className="text-base font-semibold text-foreground">New Agent</Dialog.Title>
        <Dialog.Close
          render={
            <Button variant="ghost" size="icon" aria-label="Close" disabled={submitting}>
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
          placeholder="Agent name"
        />
        <InputField
          label="Role"
          value={form.role}
          onChange={(e) => updateField('role', e.target.value)}
          error={errors.role}
          required
          placeholder="e.g. Backend Developer"
        />
        <SelectField
          label="Department"
          options={deptOptions}
          value={form.department}
          onChange={(value) => updateField('department', value)}
          error={errors.department}
          required
          placeholder="Select department..."
        />
        <SelectField
          label="Level"
          options={LEVEL_OPTIONS}
          value={form.level}
          onChange={(value) => {
            const level = parseSeniorityLevel(value)
            if (level) updateField('level', level)
          }}
        />

        <div className="flex justify-end gap-3 pt-2">
          <Dialog.Close render={<Button variant="outline" disabled={submitting}>Cancel</Button>} />
          <Button disabled={submitting} onClick={onSubmit}>
            {submitting && <Loader2 className="mr-2 size-4 animate-spin" />}
            Create Agent
          </Button>
        </div>
      </div>
    </>
  )
}

export function AgentCreateDialog({ open, onOpenChange, departments, onCreate }: AgentCreateDialogProps) {
  const [form, setForm] = useState<FormState>(INITIAL_FORM)
  const [errors, setErrors] = useState<FormErrors>({})
  const [submitting, setSubmitting] = useState(false)

  const prevOpenRef = useRef(open)
  if (!open && prevOpenRef.current) {
    setForm(INITIAL_FORM)
    setErrors({})
  }
  prevOpenRef.current = open

  const updateField: UpdateFieldFn = (key, value) => {
    setForm((prev) => ({ ...prev, [key]: value }))
    setErrors((prev) => ({ ...prev, [key]: undefined }))
  }

  const handleSubmit = useCallback(async () => {
    const next = validateAgentForm(form)
    setErrors(next)
    if (Object.keys(next).length > 0) return

    setSubmitting(true)
    try {
      const result = await onCreate({
        name: form.name.trim(),
        role: form.role.trim(),
        department: form.department,
        level: form.level,
      })
      // Store owns the toast UX; the dialog stays open on failure so the
      // user can amend their input.
      if (result === null) return
      setForm(INITIAL_FORM)
      onOpenChange(false)
    } finally {
      setSubmitting(false)
    }
  }, [form, onCreate, onOpenChange])

  const deptOptions = useMemo(
    () => departments.map((d) => ({ value: d.name, label: d.display_name ?? d.name })),
    [departments],
  )

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(v: boolean) => {
        if (!submitting) onOpenChange(v)
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-200 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
        <Dialog.Popup className={POPUP_CLASS}>
          <AgentCreateBody
            form={form}
            errors={errors}
            updateField={updateField}
            deptOptions={deptOptions}
            submitting={submitting}
            onSubmit={handleSubmit}
          />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
