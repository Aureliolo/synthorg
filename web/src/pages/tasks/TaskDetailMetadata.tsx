import { Avatar } from '@/components/ui/avatar'
import { InlineEdit } from '@/components/ui/inline-edit'
import { SelectField, type SelectOption } from '@/components/ui/select-field'
import { cn } from '@/lib/utils'
import { useTasksStore } from '@/stores/tasks'
import type { Priority } from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency, formatDate } from '@/utils/format'
import { getPriorityLabel, getTaskTypeLabel } from '@/utils/tasks'

const PRIORITIES: readonly Priority[] = ['critical', 'high', 'medium', 'low']

const PRIORITY_OPTIONS: readonly SelectOption[] = PRIORITIES.map((p) => ({
  value: p,
  label: getPriorityLabel(p),
}))

interface TaskDetailMetadataProps {
  task: Task
}

export function TaskDetailMetadata({ task }: TaskDetailMetadataProps) {
  return (
    <>
      {/* Description */}
      <div>
        <label className="text-[11px] font-semibold uppercase tracking-wider text-text-muted">
          Description
        </label>
        <InlineEdit
          value={task.description}
          onSave={async (value) => {
            // Sentinel-return contract: ``updateTask`` handles its
            // own toast UX and returns ``null`` on failure. Throwing
            // when the result is null is how InlineEdit is signalled
            // to keep the input open and show its error state.
            const updated = await useTasksStore.getState().updateTask(task.id, {
              description: value,
              expected_version: task.version,
              priority: null,
            })
            if (!updated) {
              throw new Error('Failed to save description')
            }
          }}
          className="mt-1 text-sm text-text-secondary"
        />
      </div>

      {/* Priority selector */}
      <SelectField
        label="Priority"
        options={PRIORITY_OPTIONS}
        value={task.priority}
        onChange={async (value) => {
          // Sentinel-return contract: the store owns the error toast.
          // Nothing to do on failure beyond letting the caller's UI
          // reflect the pre-change priority (the store's ``upsertTask``
          // on success already updates the select via its ``value``
          // binding).
          await useTasksStore.getState().updateTask(task.id, {
            priority: value as Priority,
            expected_version: task.version,
          })
        }}
      />

      {/* Assignee */}
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

      {/* Metadata grid */}
      <div className="grid grid-cols-3 gap-grid-gap rounded-lg border border-border p-card text-sm">
        <div>
          <span className="block text-[10px] text-text-muted">Type</span>
          <span className="text-foreground">{getTaskTypeLabel(task.type)}</span>
        </div>
        <div>
          <span className="block text-[10px] text-text-muted">Complexity</span>
          <span className="capitalize text-foreground">{task.estimated_complexity}</span>
        </div>
        <div>
          <span className="block text-[10px] text-text-muted">Project</span>
          <span className="text-foreground">{task.project}</span>
        </div>
        <div>
          <span className="block text-[10px] text-text-muted">Created</span>
          <span className="font-mono text-xs text-foreground">{formatDate(task.created_at)}</span>
        </div>
        <div>
          <span className="block text-[10px] text-text-muted">Updated</span>
          <span className="font-mono text-xs text-foreground">{formatDate(task.updated_at)}</span>
        </div>
        {task.cost != null && (
          <div>
            <span className="block text-[10px] text-text-muted">Cost</span>
            <span className="font-mono text-xs text-foreground">
              {formatCurrency(task.cost, DEFAULT_CURRENCY)}
            </span>
          </div>
        )}
      </div>

      {/* Dependencies */}
      {task.dependencies.length > 0 && (
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
      )}

      {/* Acceptance criteria */}
      {task.acceptance_criteria.length > 0 && (
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
      )}
    </>
  )
}
