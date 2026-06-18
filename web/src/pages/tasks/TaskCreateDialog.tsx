import { cloneElement, isValidElement, useId } from 'react'
import { Dialog } from '@base-ui/react/dialog'
import { Loader2, X } from 'lucide-react'
import { cn, FOCUS_RING } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import type { Complexity, Priority, TaskType } from '@/api/types/enums'
import type {
  CreateTaskRequest,
  TaskBoardSubmissionResponse,
} from '@/api/types/tasks'

import {
  useTaskCreateForm,
  type TaskCreateFormController,
  type TaskCreateFormState,
} from './useTaskCreateForm'

export interface TaskCreateDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /**
   * Resolves to the submission envelope on success (HTTP 202; the spine
   * creates the task in the background), ``null`` on failure (sentinel). The
   * dialog closes on success and stays open on ``null`` so the user can
   * correct invalid input.
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

interface TaskTemplate {
  label: string
  description: string
  defaults: Partial<TaskCreateFormState>
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

const INPUT_CLASSES = cn(
  'w-full h-8 rounded-md border border-border bg-surface px-2 text-body-sm text-foreground outline-none',
  FOCUS_RING,
)
const TEXTAREA_CLASSES = cn(
  'w-full rounded-md border border-border bg-surface px-2 py-1.5 text-body-sm text-foreground outline-none resize-y',
  FOCUS_RING,
)

export function TaskCreateDialog({ open, onOpenChange, onCreate }: TaskCreateDialogProps) {
  const ctrl = useTaskCreateForm({ open, onOpenChange, onCreate })

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next: boolean) => {
        // Prevent backdrop click / Escape from closing the dialog while a
        // create request is in flight.
        if (!ctrl.submitting) onOpenChange(next)
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
          <TaskCreateDialogHeader submitting={ctrl.submitting} />
          <TaskCreateDialogBody ctrl={ctrl} />
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}

interface TaskCreateDialogHeaderProps {
  submitting: boolean
}

function TaskCreateDialogHeader({ submitting }: TaskCreateDialogHeaderProps) {
  return (
    <div className="flex items-center justify-between mb-4">
      <Dialog.Title className="text-base font-semibold text-foreground">
        New Task
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

interface TaskCreateDialogBodyProps {
  ctrl: TaskCreateFormController
}

function TaskCreateDialogBody({ ctrl }: TaskCreateDialogBodyProps) {
  return (
    <div className="space-y-4">
      <TaskTemplatesRow onApplyTemplate={ctrl.applyTemplate} />
      <TitleDescriptionFields ctrl={ctrl} />
      <TypePriorityRow ctrl={ctrl} />
      <ProjectCreatorRow ctrl={ctrl} />
      <AssigneeComplexityRow ctrl={ctrl} />
      <FormField label="Budget Limit">
        <input
          type="number"
          value={ctrl.form.budget_limit}
          onChange={(e) => ctrl.updateField('budget_limit', e.target.value)}
          className={INPUT_CLASSES}
          placeholder="0.00"
          min="0"
          step="0.01"
        />
      </FormField>
      <TaskCreateDialogActions submitting={ctrl.submitting} onSubmit={ctrl.handleSubmit} />
    </div>
  )
}

interface TaskTemplatesRowProps {
  onApplyTemplate: (defaults: Partial<TaskCreateFormState>) => void
}

function TaskTemplatesRow({ onApplyTemplate }: TaskTemplatesRowProps) {
  return (
    <div>
      <label className="mb-1 block text-compact font-semibold uppercase tracking-wider text-text-muted">
        Start from template
      </label>
      <div className="flex flex-wrap gap-1.5">
        {TASK_TEMPLATES.map((tpl) => (
          <button
            key={tpl.label}
            type="button"
            onClick={() => onApplyTemplate(tpl.defaults)}
            className="rounded-full border border-border bg-surface px-2.5 py-1 text-compact text-text-secondary transition-colors hover:border-accent hover:text-foreground"
            title={tpl.description}
          >
            {tpl.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function TitleDescriptionFields({ ctrl }: TaskCreateDialogBodyProps) {
  return (
    <>
      <FormField label="Title" error={ctrl.errors.title} required>
        <input
          type="text"
          value={ctrl.form.title}
          onChange={(e) => ctrl.updateField('title', e.target.value)}
          className={INPUT_CLASSES}
          placeholder="Task title"
          autoFocus
        />
      </FormField>
      <FormField label="Description" error={ctrl.errors.description} required>
        <textarea
          value={ctrl.form.description}
          onChange={(e) => ctrl.updateField('description', e.target.value)}
          className={cn(TEXTAREA_CLASSES, 'min-h-[80px]')}
          placeholder="Describe the task..."
          rows={3}
        />
      </FormField>
    </>
  )
}

function TypePriorityRow({ ctrl }: TaskCreateDialogBodyProps) {
  return (
    <div className="grid grid-cols-2 gap-grid-gap max-[479px]:grid-cols-1">
      <FormField label="Type">
        <select
          value={ctrl.form.type}
          onChange={(e) => ctrl.updateField('type', e.target.value as TaskType)}
          className={INPUT_CLASSES}
        >
          {TASK_TYPES.map((t) => (
            <option key={t.value} value={t.value}>
              {t.label}
            </option>
          ))}
        </select>
      </FormField>
      <FormField label="Priority">
        <select
          value={ctrl.form.priority}
          onChange={(e) => ctrl.updateField('priority', e.target.value as Priority)}
          className={INPUT_CLASSES}
        >
          {PRIORITIES.map((p) => (
            <option key={p.value} value={p.value}>
              {p.label}
            </option>
          ))}
        </select>
      </FormField>
    </div>
  )
}

function ProjectCreatorRow({ ctrl }: TaskCreateDialogBodyProps) {
  return (
    <div className="grid grid-cols-2 gap-grid-gap">
      <FormField label="Project" error={ctrl.errors.project} required>
        <input
          type="text"
          value={ctrl.form.project}
          onChange={(e) => ctrl.updateField('project', e.target.value)}
          className={INPUT_CLASSES}
          placeholder="Project name"
        />
      </FormField>
      <FormField label="Created By" error={ctrl.errors.created_by} required>
        <input
          type="text"
          value={ctrl.form.created_by}
          onChange={(e) => ctrl.updateField('created_by', e.target.value)}
          className={INPUT_CLASSES}
          placeholder="Agent or user"
        />
      </FormField>
    </div>
  )
}

function AssigneeComplexityRow({ ctrl }: TaskCreateDialogBodyProps) {
  return (
    <div className="grid grid-cols-2 gap-grid-gap">
      <FormField label="Assigned To">
        <input
          type="text"
          value={ctrl.form.assigned_to}
          onChange={(e) => ctrl.updateField('assigned_to', e.target.value)}
          className={INPUT_CLASSES}
          placeholder="Agent name (optional)"
        />
      </FormField>
      <FormField label="Complexity">
        <select
          value={ctrl.form.estimated_complexity}
          onChange={(e) =>
            ctrl.updateField('estimated_complexity', e.target.value as Complexity)
          }
          className={INPUT_CLASSES}
        >
          {COMPLEXITIES.map((c) => (
            <option key={c.value} value={c.value}>
              {c.label}
            </option>
          ))}
        </select>
      </FormField>
    </div>
  )
}

interface TaskCreateDialogActionsProps {
  submitting: boolean
  onSubmit: () => Promise<void>
}

function TaskCreateDialogActions({ submitting, onSubmit }: TaskCreateDialogActionsProps) {
  return (
    <div className="flex justify-end gap-3 pt-2">
      <Dialog.Close
        render={
          <Button variant="outline" disabled={submitting}>
            Cancel
          </Button>
        }
      />
      <Button disabled={submitting} onClick={onSubmit}>
        {submitting && <Loader2 className="mr-2 size-4 animate-spin" />}
        Create Task
      </Button>
    </div>
  )
}

interface FormFieldProps {
  label: string
  error?: string | undefined
  required?: boolean | undefined
  children: React.ReactNode
}

function FormField({ label, error, required, children }: FormFieldProps) {
  // Accessibility: the <label> wraps only the visible text and the form
  // control so screen readers resolve label-to-input via implicit association
  // without the error text leaking into the control's accessible name. The
  // error <p> is rendered as a sibling with a stable id, and the form control
  // is cloned with aria-describedby pointing at it.
  const errorId = useId()
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
          {label}
          {required && <span className="text-danger"> *</span>}
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
