import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { GitBranch } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ConditionalNodeData extends Record<string, unknown> {
  label: string
  config: Record<string, unknown>
  selected?: boolean
  hasError?: boolean
}

export type ConditionalNodeType = Node<ConditionalNodeData, 'conditional'>

function ConditionalNodeComponent({ data, selected }: NodeProps<ConditionalNodeType>) {
  const rawCondition = data.config['condition_expression']
  const condition = typeof rawCondition === 'string' && rawCondition.trim() ? rawCondition : data.label

  return (
    <div
      className={cn(
        'relative flex size-24 items-center justify-center',
        selected && '[&>div]:ring-2 [&>div]:ring-accent',
        data.hasError && '[&>div]:ring-2 [&>div]:ring-danger',
      )}
      data-testid="conditional-node"
      aria-label={`Conditional: ${condition}`}
    >
      {/* Diamond shape */}
      <div className="absolute inset-2 rotate-45 rounded-sm border border-warning/50 bg-warning/10" />

      {/* Content (counter-rotated) */}
      <div className="relative z-10 flex flex-col items-center gap-0.5 px-1">
        <GitBranch className="size-3.5 text-warning" aria-hidden="true" />
        <span className="max-w-16 truncate text-center font-sans text-micro font-medium text-foreground">
          {condition}
        </span>
      </div>

      <Handle type="target" position={Position.Top} className="bg-border-bright! size-1.5!" />
      {/* True branch (right) */}
      <Handle
        type="source"
        position={Position.Right}
        id="true"
        className="bg-success! size-1.5!"
      />
      {/* False branch (bottom) */}
      <Handle
        type="source"
        position={Position.Bottom}
        id="false"
        className="bg-danger! size-1.5!"
      />
    </div>
  )
}

export const ConditionalNode = memo(ConditionalNodeComponent)
