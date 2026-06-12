import { memo, useMemo } from 'react'
import { useDroppable } from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { Inbox } from 'lucide-react'
import { cn, type SemanticColor } from '@/lib/utils'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { formatNumber } from '@/utils/format'
import { TaskCard } from './TaskCard'
import type { KanbanColumn } from '@/utils/tasks'
import type { Task } from '@/api/types/tasks'

const COLOR_CLASSES: Record<SemanticColor | 'text-secondary', string> = {
  success: 'bg-success',
  accent: 'bg-accent',
  warning: 'bg-warning',
  danger: 'bg-danger',
  'text-secondary': 'bg-text-secondary',
}

// Rough workload-hour estimate per complexity bucket. Hoisted to module scope
// so it is not rebuilt on every TaskColumn render. Unknown complexity values
// (e.g. backend drift) fall back to 0 via the `??` below.
const COMPLEXITY_HOURS: Record<string, number> = {
  simple: 1,
  medium: 3,
  complex: 8,
  epic: 24,
}

export interface TaskColumnProps {
  column: KanbanColumn
  tasks: Task[]
  onSelectTask: (taskId: string) => void
  /**
   * Whether this column contains the currently-selected task. The
   * column header gets a subtle highlight to help operators map the
   * detail-drawer-open task back to its lifecycle phase on the board.
   */
  highlighted?: boolean
}

const SortableTaskCard = memo(function SortableTaskCard({
  task,
  onSelectTask,
}: {
  task: Task
  onSelectTask: (id: string) => void
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: task.id, data: { task, status: task.status } })

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  }

  return (
    <div ref={setNodeRef} style={style} {...attributes} {...listeners}>
      <TaskCard task={task} onSelect={onSelectTask} isDragging={isDragging} />
    </div>
  )
})

export function TaskColumn({ column, tasks, onSelectTask, highlighted }: TaskColumnProps) {
  const { setNodeRef, isOver } = useDroppable({
    id: column.id,
    data: { columnId: column.id, statuses: column.statuses },
  })

  const taskIds = useMemo(() => tasks.map((t) => t.id), [tasks])

  // Memoised so the reduce doesn't re-run on every drag-over `isOver` flip,
  // which is unrelated to the task list.
  const estimatedHours = useMemo(
    () => tasks.reduce((sum, t) => sum + (COMPLEXITY_HOURS[t.estimated_complexity] ?? 0), 0),
    [tasks],
  )

  return (
    <section
      className={cn(
        'flex w-72 shrink-0 snap-start flex-col rounded-lg transition-colors',
        highlighted && 'bg-accent/5 ring-1 ring-accent/30',
      )}
      data-column-id={column.id}
      data-selected-column={highlighted ? '' : undefined}
      aria-labelledby={`task-column-${column.id}-label`}
    >
      {/* Column header */}
      <div className="mb-3 flex items-center gap-2 px-1">
        <span
          className={cn('size-2 rounded-full', COLOR_CLASSES[column.color])}
          aria-hidden="true"
        />
        <span
          id={`task-column-${column.id}-label`}
          className="text-[13px] font-semibold text-foreground"
        >
          {column.label}
        </span>
        <span
          className="rounded-full bg-surface px-1.5 py-0.5 text-[length:var(--so-text-micro)] font-mono text-text-muted"
          aria-label={`${tasks.length} task${tasks.length === 1 ? '' : 's'}`}
        >
          {formatNumber(tasks.length)}
        </span>
        {estimatedHours > 0 && (
          <span
            className="ml-auto text-[length:var(--so-text-micro)] font-mono text-text-muted"
            aria-label={`Estimated workload: approximately ${formatNumber(estimatedHours)} hours`}
            title="Rough workload based on task complexity (simple=1h, medium=3h, complex=8h, epic=24h)"
          >
            ~{formatNumber(estimatedHours)}h
          </span>
        )}
      </div>

      {/* Droppable zone */}
      <div
        ref={setNodeRef}
        className={cn(
          'flex min-h-[120px] flex-1 flex-col gap-2 rounded-lg border border-transparent p-1 transition-colors',
          isOver && 'border-accent bg-accent/5',
        )}
      >
        <SortableContext items={taskIds} strategy={verticalListSortingStrategy}>
          {tasks.length > 0 ? (
            <StaggerGroup className="flex flex-col gap-2">
              {tasks.map((task) => (
                <StaggerItem key={task.id}>
                  <SortableTaskCard task={task} onSelectTask={onSelectTask} />
                </StaggerItem>
              ))}
            </StaggerGroup>
          ) : (
            <EmptyState
              icon={Inbox}
              title="No tasks"
              description={`No tasks in ${column.label.toLowerCase()}`}
              className="py-8"
            />
          )}
        </SortableContext>
      </div>
    </section>
  )
}
