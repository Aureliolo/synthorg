import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { Merge } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface ParallelJoinNodeData extends Record<string, unknown> {
  label: string
  config: Record<string, unknown>
  selected?: boolean
  hasError?: boolean
}

export type ParallelJoinNodeType = Node<ParallelJoinNodeData, 'parallel_join'>

function ParallelJoinNodeComponent({ data, selected }: NodeProps<ParallelJoinNodeType>) {
  const strategy = (data.config.join_strategy as string) || 'all'

  return (
    <div
      className={cn(
        'flex h-8 min-w-32 items-center justify-center gap-1.5 rounded-md border border-accent/40 bg-accent/5 px-4',
        selected && 'ring-2 ring-accent',
        data.hasError && 'ring-2 ring-danger',
      )}
      data-testid="parallel-join-node"
      aria-label={`Parallel Join (${strategy}): ${data.label}`}
    >
      {/* Multiple target handles along the top */}
      <Handle
        type="target"
        position={Position.Top}
        id="branch-0"
        className="bg-accent! size-1.5!"
        style={{ left: '33%' }}
      />
      <Handle
        type="target"
        position={Position.Top}
        id="branch-1"
        className="bg-accent! size-1.5!"
        style={{ left: '66%' }}
      />

      <Merge className="size-3.5 text-accent" aria-hidden="true" />
      <span className="font-sans text-micro font-medium text-foreground">
        Join ({strategy})
      </span>

      <Handle type="source" position={Position.Bottom} className="bg-border-bright! size-1.5!" />
    </div>
  )
}

export const ParallelJoinNode = memo(ParallelJoinNodeComponent)
