import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { ClipboardList } from 'lucide-react'
import { PriorityBadge } from '@/components/ui/task-status-indicator'
import { cn } from '@/lib/utils'
import type { Priority } from '@/api/types/enums'

export interface TaskNodeData extends Record<string, unknown> {
  label: string
  config: Record<string, unknown>
  selected?: boolean
  hasError?: boolean
}

export type TaskNodeType = Node<TaskNodeData, 'task'>

const VALID_PRIORITIES = new Set<string>(['critical', 'high', 'medium', 'low'])

interface TaskNodeFields {
  title: string
  priority: Priority | undefined
  taskType: string | undefined
}

function extractTaskNodeFields(data: TaskNodeData): TaskNodeFields {
  const title = nonEmptyString(data.config?.title) ?? data.label
  const priority = resolvePriority(data.config?.priority)
  const taskType = nonEmptyString(data.config?.task_type)
  return { title, priority, taskType }
}

function nonEmptyString(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined
}

function resolvePriority(value: unknown): Priority | undefined {
  if (typeof value !== 'string') return undefined
  return VALID_PRIORITIES.has(value) ? (value as Priority) : undefined
}

function TaskNodeComponent({ data, selected }: NodeProps<TaskNodeType>) {
  const { title, priority, taskType } = extractTaskNodeFields(data)
  return (
    <div
      className={cn(
        'min-w-40 max-w-56 rounded-lg border border-border bg-card p-card-tight',
        selected && 'ring-2 ring-accent',
        data.hasError && 'ring-2 ring-danger',
      )}
      data-testid="task-node"
      aria-label={`Task: ${title}`}
    >
      <Handle type="target" position={Position.Top} className="bg-border-bright! size-1.5!" />
      <div className="flex items-start gap-2">
        <ClipboardList className="mt-0.5 size-3.5 shrink-0 text-accent" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate font-sans text-xs font-semibold text-foreground">
              {title}
            </span>
            {priority && <PriorityBadge priority={priority} />}
          </div>
          {taskType && (
            <span className="block truncate font-sans text-micro text-muted-foreground">
              {taskType}
            </span>
          )}
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="bg-border-bright! size-1.5!"
      />
    </div>
  )
}

export const TaskNode = memo(TaskNodeComponent)
