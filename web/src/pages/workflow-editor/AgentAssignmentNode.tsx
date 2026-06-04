import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { UserCheck } from 'lucide-react'
import { cn } from '@/lib/utils'

export interface AgentAssignmentNodeData extends Record<string, unknown> {
  label: string
  config: Record<string, unknown>
  selected?: boolean
  hasError?: boolean
}

export type AgentAssignmentNodeType = Node<AgentAssignmentNodeData, 'agent_assignment'>

function AgentAssignmentNodeComponent({ data, selected }: NodeProps<AgentAssignmentNodeType>) {
  const strategy = (data.config.routing_strategy as string) || 'auto'
  const role = data.config.role_filter as string | undefined

  return (
    <div
      className={cn(
        'min-w-36 max-w-48 rounded-lg border border-accent/30 bg-accent/5 px-3 py-2',
        selected && 'ring-2 ring-accent',
        data.hasError && 'ring-2 ring-danger',
      )}
      data-testid="agent-assignment-node"
      aria-label={`Agent Assignment: ${strategy}`}
    >
      <Handle type="target" position={Position.Top} className="bg-border-bright! size-1.5!" />

      <div className="flex items-center gap-2">
        <UserCheck className="size-3.5 shrink-0 text-accent" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <span className="block truncate font-sans text-xs font-semibold text-foreground">
            {strategy}
          </span>
          {role && (
            <span className="block truncate font-sans text-micro text-muted-foreground">
              {role}
            </span>
          )}
        </div>
      </div>

      <Handle type="source" position={Position.Bottom} className="bg-border-bright! size-1.5!" />
    </div>
  )
}

export const AgentAssignmentNode = memo(AgentAssignmentNodeComponent)
