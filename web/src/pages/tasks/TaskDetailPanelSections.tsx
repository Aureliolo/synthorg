import { Calendar, GitBranch, Loader2, Tag, Layers, User } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Avatar } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { InlineEdit } from '@/components/ui/inline-edit'
import { PriorityBadge } from '@/components/ui/task-status-indicator'
import {
  getAvailableTransitions,
  getPriorityLabel,
  getTaskStatusLabel,
  getTaskTypeLabel,
} from '@/utils/tasks'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency, formatDateTime } from '@/utils/format'
import type { Priority, TaskStatus } from '@/api/types/enums'
import type { DashboardTask, UpdateTaskRequest } from '@/api/types/tasks'

const PRIORITIES: Priority[] = ['critical', 'high', 'medium', 'low']

export interface TaskDetailFooterProps {
  task: DashboardTask
  onCancelClick: () => void
  onDeleteClick: () => void
}

export function TaskDetailPanelFooter({
  task,
  onCancelClick,
  onDeleteClick,
}: TaskDetailFooterProps) {
  const showCancel = task.status !== 'cancelled' && task.status !== 'completed'
  return (
    <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-3">
      {showCancel && (
        <Button variant="outline" size="sm" onClick={onCancelClick}>
          Cancel Task
        </Button>
      )}
      <Button variant="destructive" size="sm" onClick={onDeleteClick}>
        Delete
      </Button>
    </div>
  )
}

interface TaskUpdateProps {
  task: DashboardTask
  onUpdate: (taskId: string, data: UpdateTaskRequest) => Promise<void>
}

export function DescriptionEdit({ task, onUpdate }: TaskUpdateProps) {
  return (
    <div>
      <label className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        Description
      </label>
      <InlineEdit
        value={task.description}
        onSave={async (value) => {
          await onUpdate(task.id, { description: value, expected_version: task.version })
        }}
        className="mt-1 text-sm text-text-secondary"
      />
    </div>
  )
}

export function PrioritySection({ task, onUpdate }: TaskUpdateProps) {
  return (
    <div>
      <label className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        Priority
      </label>
      <div className="mt-1 flex items-center gap-2">
        <PriorityBadge priority={task.priority} />
        <select
          value={task.priority}
          onChange={(e) => {
            void onUpdate(task.id, {
              priority: e.target.value as Priority,
              expected_version: task.version,
            })
          }}
          className="h-7 rounded border border-border bg-surface px-1.5 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-accent"
          aria-label="Change priority"
        >
          {PRIORITIES.map((p) => (
            <option key={p} value={p}>
              {getPriorityLabel(p)}
            </option>
          ))}
        </select>
      </div>
    </div>
  )
}

export function AssigneeSection({ task, onUpdate }: TaskUpdateProps) {
  return (
    <div>
      <div className="flex items-center gap-2">
        <User className="size-4 text-text-muted" />
        <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
          Assignee
        </span>
      </div>
      <div className="mt-1 flex items-center gap-2">
        {task.assigned_to && <Avatar name={task.assigned_to} size="sm" />}
        <InlineEdit
          value={task.assigned_to ?? ''}
          onSave={async (value) => {
            await onUpdate(task.id, {
              assigned_to: value.trim() || undefined,
              expected_version: task.version,
            })
          }}
          className="text-sm"
          placeholder="Unassigned"
        />
      </div>
    </div>
  )
}

interface TaskOnlyProps {
  task: DashboardTask
}

interface MetaGridProps extends TaskOnlyProps {
  /** Currency code for the task-cost display (e.g. ``'USD'``). Optional so
   * existing callers keep working with DEFAULT_CURRENCY; new code should
   * thread the active tenant / user / workspace currency through (regional-
   * defaults: no region/currency is privileged in framework code). Mirrors
   * the TaskCard ``currency`` prop. */
  currency?: string
}

export function MetaGrid({ task, currency }: MetaGridProps) {
  const displayCurrency = currency ?? DEFAULT_CURRENCY
  return (
    <div className="grid grid-cols-2 gap-grid-gap rounded-lg border border-border p-card">
      <MetaField icon={Tag} label="Type" value={getTaskTypeLabel(task.type)} />
      <MetaField icon={Layers} label="Complexity" value={task.estimated_complexity} />
      <MetaField icon={Calendar} label="Created" value={formatDateTime(task.created_at)} />
      <MetaField icon={Calendar} label="Updated" value={formatDateTime(task.updated_at)} />
      {task.deadline && (
        <MetaField icon={Calendar} label="Deadline" value={formatDateTime(task.deadline)} />
      )}
      {task.cost != null && (
        <MetaField
          icon={Tag}
          label="Cost"
          value={formatCurrency(task.cost, displayCurrency)}
        />
      )}
    </div>
  )
}

export function DependenciesList({ task }: TaskOnlyProps) {
  return (
    <div>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        <GitBranch className="size-3.5" />
        Dependencies ({task.dependencies.length})
      </div>
      <ul className="mt-1.5 space-y-1">
        {task.dependencies.map((depId) => (
          <li
            key={depId}
            className="rounded border border-border px-2 py-1 font-mono text-xs text-text-secondary"
          >
            {depId}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function AcceptanceCriteriaList({ task }: TaskOnlyProps) {
  return (
    <div>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        Acceptance Criteria
      </span>
      <ul className="mt-1.5 space-y-1">
        {task.acceptance_criteria.map((criterion, idx) => (
          <li
            // eslint-disable-next-line @eslint-react/no-array-index-key -- criteria lack unique IDs; descriptions may duplicate
            key={`${criterion.description}-${idx}`}
            className="flex items-start gap-2 text-xs text-text-secondary"
          >
            <span
              className={cn(
                'mt-0.5 size-3.5 shrink-0 rounded border',
                criterion.met ? 'border-success bg-success/20' : 'border-border',
              )}
            />
            {criterion.description}
          </li>
        ))}
      </ul>
    </div>
  )
}

export interface TransitionsSectionProps {
  task: DashboardTask
  transitioning: TaskStatus | null
  onTransition: (targetStatus: TaskStatus) => Promise<void>
}

export function TransitionsSection({
  task,
  transitioning,
  onTransition,
}: TransitionsSectionProps) {
  const availableTransitions = getAvailableTransitions(task.status)
  if (availableTransitions.length === 0) return null
  return (
    <div>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        Transitions
      </span>
      <div className="mt-1.5 flex flex-wrap gap-2">
        {availableTransitions.map((targetStatus) => (
          <Button
            key={targetStatus}
            size="sm"
            variant="outline"
            disabled={transitioning !== null}
            onClick={() => void onTransition(targetStatus)}
          >
            {transitioning === targetStatus && (
              <Loader2 className="mr-1 size-3 animate-spin" />
            )}
            {getTaskStatusLabel(targetStatus)}
          </Button>
        ))}
      </div>
    </div>
  )
}

interface MetaFieldProps {
  icon: typeof Tag
  label: string
  value: string
}

function MetaField({ icon: Icon, label, value }: MetaFieldProps) {
  return (
    <div className="flex items-start gap-2">
      <Icon className="mt-0.5 size-3.5 text-text-muted" aria-hidden="true" />
      <div>
        <span className="block text-[10px] text-text-muted">{label}</span>
        <span className="block text-xs capitalize text-foreground">{value}</span>
      </div>
    </div>
  )
}
