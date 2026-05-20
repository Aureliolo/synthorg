import { cloneElement, isValidElement, useCallback, useId, useRef, useState } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { Loader2, X } from 'lucide-react'
import { cn, FOCUS_RING } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import type { Complexity, Priority, TaskType } from '@/api/types/enums'
import type { CreateTaskRequest, TaskBoardSubmissionResponse } from '@/api/types/tasks'

export interface TaskCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /**
   * Resolves to the submission envelope on success (HTTP 202; the
   * spine creates the task in the background), ``null`` on failure
   * (sentinel). The dialog closes on success and stays open on
   * ``null`` so the user can correct invalid input.
   */
  onCreate: (data: CreateTaskRequest) => Promise<TaskBoardSubmissionResponse | null>
}

const TASK_TYPES: { value: TaskType; label: string }[] = [
  { value: 'development', label: 'Development' },
  { value: 'design', label: 'Design' },
  { value: 'research', label: 'Research' },
  { value: 'review', label: 'Review' },
  { value: 'meeting', label: 'Meeting' },
  { value: 'admin', label: 'Admin' },
]

const PRIORITIES: { value: Priority; label: string }[] = [
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'medium', label: 'Medium' },
  { value: 'low', label: 'Low' },
]

const COMPLEXITIES: { value: Complexity; label: string }[] = [
  { value: 'simple', label: 'Simple' },
  { value: 'medium', label: 'Medium' },
  { value: 'complex', label: 'Complex' },
  { value: 'epic', label: 'Epic' },
]

interface FormState {
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

interface TaskTemplate {
  label: string
  description: string
  defaults: Partial<FormState>
}

const TASK_TEMPLATES: TaskTemplate[] = [
  {
    label: 'Development',
    description: 'Code implementation task',
    defaults: { type: 'development', estimated_complexity: 'medium', priority: 'medium' },
  },
  {
    label: 'Bug Fix',
    description: 'Fix a reported issue',
    defaults: { type: 'development', estimated_complexity: 'simple', priority: 'high' },
  },
  {
    label: 'Research',
    description: 'Investigate or evaluate an approach',
    defaults: { type: 'research', estimated_complexity: 'medium', priority: 'medium' },
  },
  {
    label: 'Code Review',
    description: 'Review submitted work',
    defaults: { type: 'review', estimated_complexity: 'simple', priority: 'medium' },
  },
]

const INITIAL_FORM: FormState = {
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

export function TaskCreateDialog({ open, onOpenChange, onCreate }: TaskCreateDialogProps) {
  const [form, setForm] = useState<FormState>(INITIAL_FORM)
  const [errors, setErrors] = useState<Partial<Record<keyof FormState, string>>>({})
  const [submitting, setSubmitting] = useState(false)

  // Reset form state on close (render-phase check mirroring AgentCreateDialog /
  // DepartmentCreateDialog / PackSelectionDialog so reopening does not show
  // stale input from the previous session).
  const prevOpenRef = useRef(open)
  if (!open && prevOpenRef.current) {
    setForm(INITIAL_FORM)
    setErrors({})
  }
  prevOpenRef.current = open

  function updateField<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((prev) => ({ ...prev, [key]: value }))
    setErrors((prev) => ({ ...prev, [key]: undefined }))
  }

  function validate(): boolean {
    const next: Partial<Record<keyof FormState, string>> = {}
    if (!form.title.trim()) next.title = 'Title is required'
    if (!form.description.trim()) next.description = 'Description is required'
    if (!form.project.trim()) next.project = 'Project is required'
    if (!form.created_by.trim()) next.created_by = 'Creator is required'
    if (form.budget_limit !== '') {
      const n = Number(form.budget_limit)
      if (!Number.isFinite(n) || n < 0) next.budget_limit = 'Budget must be a non-negative number'
    }
    setErrors(next)
    return Object.keys(next).length === 0
  }

  const handleSubmit = useCallback(async () => {
    if (!validate()) return
    setSubmitting(true)
    try {
      const data: CreateTaskRequest = {
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
      // Sentinel-return: the store owns the error UX (toast + log) on
      // failure, so we only null-check here. ``null`` keeps the dialog
      // open so the user doesn't lose their filled-in form. The
      // success value is a ``TaskBoardSubmissionResponse`` (HTTP 202):
      // the spine creates the actual task asynchronously and the board
      // inserts it via the ``task.created`` WS event.
      const submission = await onCreate(data)
      if (submission === null) return
      setForm(INITIAL_FORM)
      onOpenChange(false)
    } finally {
      setSubmitting(false)
    }
  // eslint-disable-next-line @eslint-react/exhaustive-deps -- validate reads form which is in deps
  }, [form, onCreate, onOpenChange])

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next: boolean) => {
        // Prevent backdrop click / Escape from closing the dialog while a
        // create request is in flight, matching the guard pattern used by
        // the other create dialogs.
        if (!submitting) onOpenChange(next)
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-background/80 backdrop-blur-sm transition-opacity duration-200 ease-out data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0" />
        <Dialog.Popup
          className={cn(
            'fixed top-1/2 left-1/2 z-50 w-full max-w-lg md:max-w-2xl -translate-x-1/2 -translate-y-1/2',
            'rounded-xl border border-border-bright bg-surface p-card-tight sm:p-card md:p-card-roomy shadow-[var(--so-shadow-card-hover)]',
            'transition-[opacity,translate,scale] duration-200 ease-out',
            'data-[closed]:opacity-0 data-[starting-style]:opacity-0 data-[ending-style]:opacity-0',
            'data-[closed]:scale-95 data-[starting-style]:scale-95 data-[ending-style]:scale-95',
            'max-h-[85vh] overflow-y-auto sm:max-h-[80vh]',
          )}
        >
          <div className="flex items-center justify-between mb-4">
            <Dialog.Title className="text-base font-semibold text-foreground">
              New Task
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
            {/* Template suggestions */}
            <div>
              <label className="mb-1 block text-compact font-semibold uppercase tracking-wider text-text-muted">
                Start from template
              </label>
              <div className="flex flex-wrap gap-1.5">
                {TASK_TEMPLATES.map((tpl) => (
                  <button
                    key={tpl.label}
                    type="button"
                    onClick={() => setForm((prev) => ({ ...prev, ...tpl.defaults }))}
                    className="rounded-full border border-border bg-surface px-2.5 py-1 text-compact text-text-secondary transition-colors hover:border-accent hover:text-foreground"
                    title={tpl.description}
                  >
                    {tpl.label}
                  </button>
                ))}
              </div>
            </div>

            <FormField label="Title" error={errors.title} required>
              <input
                type="text"
                value={form.title}
                onChange={(e) => updateField('title', e.target.value)}
                className={INPUT_CLASSES}
                placeholder="Task title"
                autoFocus
              />
            </FormField>

            <FormField label="Description" error={errors.description} required>
              <textarea
                value={form.description}
                onChange={(e) => updateField('description', e.target.value)}
                className={cn(TEXTAREA_CLASSES, 'min-h-[80px]')}
                placeholder="Describe the task..."
                rows={3}
              />
            </FormField>

            <div className="grid grid-cols-2 gap-grid-gap">
              <FormField label="Type">
                <select value={form.type} onChange={(e) => updateField('type', e.target.value as TaskType)} className={INPUT_CLASSES}>
                  {TASK_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                </select>
              </FormField>

              <FormField label="Priority">
                <select value={form.priority} onChange={(e) => updateField('priority', e.target.value as Priority)} className={INPUT_CLASSES}>
                  {PRIORITIES.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
                </select>
              </FormField>
            </div>

            <div className="grid grid-cols-2 gap-grid-gap">
              <FormField label="Project" error={errors.project} required>
                <input type="text" value={form.project} onChange={(e) => updateField('project', e.target.value)} className={INPUT_CLASSES} placeholder="Project name" />
              </FormField>

              <FormField label="Created By" error={errors.created_by} required>
                <input type="text" value={form.created_by} onChange={(e) => updateField('created_by', e.target.value)} className={INPUT_CLASSES} placeholder="Agent or user" />
              </FormField>
            </div>

            <div className="grid grid-cols-2 gap-grid-gap">
              <FormField label="Assigned To">
                <input type="text" value={form.assigned_to} onChange={(e) => updateField('assigned_to', e.target.value)} className={INPUT_CLASSES} placeholder="Agent name (optional)" />
              </FormField>

              <FormField label="Complexity">
                <select value={form.estimated_complexity} onChange={(e) => updateField('estimated_complexity', e.target.value as Complexity)} className={INPUT_CLASSES}>
                  {COMPLEXITIES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                </select>
              </FormField>
            </div>

            <FormField label="Budget Limit">
              <input type="number" value={form.budget_limit} onChange={(e) => updateField('budget_limit', e.target.value)} className={INPUT_CLASSES} placeholder="0.00" min="0" step="0.01" />
            </FormField>

            <div className="flex justify-end gap-3 pt-2">
              <Dialog.Close
                render={
                  <Button variant="outline" disabled={submitting}>Cancel</Button>
                }
              />
              <Button disabled={submitting} onClick={handleSubmit}>
                {submitting && <Loader2 className="mr-2 size-4 animate-spin" />}
                Create Task
              </Button>
            </div>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

const INPUT_CLASSES = cn('w-full h-8 rounded-md border border-border bg-surface px-2 text-body-sm text-foreground outline-none', FOCUS_RING)
const TEXTAREA_CLASSES = cn('w-full rounded-md border border-border bg-surface px-2 py-1.5 text-body-sm text-foreground outline-none resize-y', FOCUS_RING)

function FormField({ label, error, required, children }: { label: string; error?: string; required?: boolean; children: React.ReactNode }) {
  // Accessibility:
  // - The <label> wraps only the visible text and the form control so
  //   screen readers resolve label-to-input via implicit association
  //   without the error text leaking into the control's accessible name.
  // - The error <p> is rendered as a sibling of the label (outside it)
  //   with a stable id, and the form control is cloned with an
  //   `aria-describedby` pointing at that id so AT announces the error
  //   as separate help text rather than as part of the label.
  const errorId = useId()
  // Inject aria-describedby / aria-invalid onto the wrapped form control
  // when an error is present so AT announces the error as separate help
  // text. cloneElement is the only way to do this for an arbitrary
  // children prop without binding every call site to a specific input
  // component; the wrapping `isValidElement` guard keeps the clone safe
  // for the single-element case this FormField is actually used for.
  const controlWithAria =
    error && isValidElement<{ 'aria-describedby'?: string; 'aria-invalid'?: boolean }>(children)
      ? // eslint-disable-next-line @eslint-react/no-clone-element -- see comment above
        cloneElement(children, {
          'aria-describedby': errorId,
          'aria-invalid': true,
        })
      : children
  return (
    <div className="block">
      <label className="block">
        <span className="mb-1 block text-compact font-semibold uppercase tracking-wider text-text-muted">
          {label}{required && <span className="text-danger"> *</span>}
        </span>
        {controlWithAria}
      </label>
      {error && (
        <p id={errorId} className="mt-0.5 text-micro text-danger">
          {error}
        </p>
      )}
    </div>
  )
}

export { INPUT_CLASSES, TEXTAREA_CLASSES }
