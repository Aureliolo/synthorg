import { Avatar } from '@/components/ui/avatar'
import { InlineEdit } from '@/components/ui/inline-edit'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { cn } from '@/lib/utils'
import { useTasksStore } from '@/stores/tasks'
import type { Priority } from '@/api/types/enums'
import type { DashboardTask } from '@/api/types/tasks'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency, formatDateTime } from '@/utils/format'
import { getPriorityLabel, getTaskTypeLabel } from '@/utils/tasks'

const PRIORITIES: readonly Priority[] = ['critical', 'high', 'medium', 'low']

const PRIORITY_OPTIONS: readonly SelectOption[] = PRIORITIES.map((p) => ({
  value: p,
  label: getPriorityLabel(p),
}))

interface TaskDetailMetadataProps {
  task: DashboardTask
}

export function TaskDetailMetadata({ task }: TaskDetailMetadataProps) {
  return (
    <>
      <DescriptionField task={task} />
      <PriorityField task={task} />
      <AssigneeField task={task} />
      <MetadataGrid task={task} />
      {task.dependencies.length > 0 && <DependenciesList task={task} />}
      {task.acceptance_criteria.length > 0 && <AcceptanceCriteriaList task={task} />}
    </>
  )
}

interface TaskFieldProps {
  task: DashboardTask
}

function DescriptionField({ task }: TaskFieldProps) {
  return (
    <div>
      <label className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        Description
      </label>
      <InlineEdit
        value={task.description}
        onSave={async (value) => {
          // Sentinel-return contract: ``updateTask`` handles its own toast UX
          // and returns ``null`` on failure. Throwing when the result is null
          // is how InlineEdit is signalled to keep the input open and show
          // its error state. Omit priority entirely so a description-only edit
          // never inadvertently clears the task's existing priority.
          const updated = await useTasksStore.getState().updateTask(task.id, {
            description: value,
            expected_version: task.version ?? null,
          })
          if (!updated) {
            throw new Error('Failed to save description')
          }
        }}
        className="mt-1 text-sm text-text-secondary"
      />
    </div>
  )
}

function PriorityField({ task }: TaskFieldProps) {
  return (
    <SelectField
      label="Priority"
      options={PRIORITY_OPTIONS}
      value={task.priority}
      onChange={async (value) => {
        // Sentinel-return contract: the store owns the error toast. The select
        // re-binds to the latest priority via its ``value`` prop on the next
        // render after the store's ``upsertTask`` succeeds.
        await useTasksStore.getState().updateTask(task.id, {
          priority: value as Priority,
          expected_version: task.version ?? null,
        })
      }}
    />
  )
}

function AssigneeField({ task }: TaskFieldProps) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        Assignee:
      </span>
      {task.assigned_to ? (
        <span className="flex items-center gap-1.5">
          <Avatar name={task.assigned_to} size="sm" />
          <span className="text-sm text-foreground">{task.assigned_to}</span>
        </span>
      ) : (
        <span className="text-sm text-text-muted">Unassigned</span>
      )}
    </div>
  )
}

function MetadataGrid({ task }: TaskFieldProps) {
  return (
    <div className="grid grid-cols-3 gap-grid-gap rounded-lg border border-border p-card text-sm">
      <MetadataCell label="Type" value={getTaskTypeLabel(task.type)} />
      <MetadataCell
        label="Complexity"
        value={task.estimated_complexity}
        capitalize
      />
      <MetadataCell label="Project" value={task.project} />
      <MetadataCell
        label="Created"
        value={formatDateTime(task.created_at)}
        monospace
      />
      <MetadataCell
        label="Updated"
        value={formatDateTime(task.updated_at)}
        monospace
      />
      {task.cost != null && (
        <MetadataCell
          label="Cost"
          value={formatCurrency(task.cost, DEFAULT_CURRENCY)}
          monospace
        />
      )}
    </div>
  )
}

interface MetadataCellProps {
  label: string
  value: string
  capitalize?: boolean
  monospace?: boolean
}

function MetadataCell({ label, value, capitalize, monospace }: MetadataCellProps) {
  return (
    <div>
      <span className="block text-[10px] text-text-muted">{label}</span>
      <span
        className={cn(
          'text-foreground',
          capitalize && 'capitalize',
          monospace && 'font-mono text-xs',
        )}
      >
        {value}
      </span>
    </div>
  )
}

function DependenciesList({ task }: TaskFieldProps) {
  return (
    <div>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
        Dependencies ({task.dependencies.length})
      </span>
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

function AcceptanceCriteriaList({ task }: TaskFieldProps) {
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
            className="flex items-start gap-2 text-sm text-text-secondary"
          >
            <span
              className={cn(
                'mt-0.5 size-4 shrink-0 rounded border',
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
